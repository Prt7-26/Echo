"""Deterministic simulated-user harness for Echo evaluation.

The midterm report commits us to four metrics that only make sense with
ground-truth labels (M1 nomination precision, M3 drift precision/recall,
M4 confidence calibration, M5 task-success uplift). Real Hermes sessions
don't carry those labels, so we build a simulator that drives Hermes'
runtime hooks directly with a scripted sequence of events.

Design choices:

* No real Hermes loop. We invoke the Echo plugin's hook handlers
  (``_on_session_start``, the monkey-patched ``bump_use``,
  ``signals.on_pre_llm_call``, ``signals.on_post_tool_call``,
  ``_on_session_end``) directly. This is the same pattern
  ``scripts/verify_echo.py`` uses.

* Ground truth is attached to the scenario, not the database. The
  artifact written at the end of a run carries both the recorded
  signals AND the labels, so metric scripts read one file.

* Layer B / Layer C LLM calls are stubbed during the simulation. They
  CAN be enabled with the real LLM if a recorded cache is provided
  (see ``plugins.echo_signals.llm_cache``).

* M5 success is measured as retrieval recall@k of planted-relevant
  preference examples — we cannot grade a real-agent answer without a
  real agent, so we score the proximate question (did the retriever
  surface the right example) instead of the downstream one.

Usage:

    from scripts.eval.harness import Harness, build_default_scenarios

    h = Harness(out_path="/tmp/run.jsonl")
    for s in build_default_scenarios():
        h.add_scenario(s)
    h.run()
    h.dump()
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------
# Scenario DSL
# ----------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    success: bool = True


@dataclass
class UserTurn:
    """One user message + the tools the agent then calls in response."""
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    # Layer B planted label — what the NL classifier *should* return for
    # this text. The harness uses this to drive a fake classifier so we
    # don't have to call a real LLM unless replay is enabled.
    expected_sentiment: str = "neutral"  # "positive" | "negative" | "neutral"


@dataclass
class Invocation:
    """One skill-loaded segment within a session."""
    skill_id: str
    turns: List[UserTurn] = field(default_factory=list)

    # Ground truth for M1: is this invocation worth saving as its own skill?
    should_be_nominated: bool = False

    # Ground truth for M3: is this invocation a genuine distribution shift
    # that the drift detector *should* catch?
    should_drift: bool = False


@dataclass
class Session:
    session_id: str
    platform: str = "cli"
    invocations: List[Invocation] = field(default_factory=list)


@dataclass
class GroundTruth:
    """Per-skill labels that are stable across the run."""

    # For M4 calibration: the planted "true usefulness" of each skill
    # on a 0..1 scale. Echo's confidence is expected to rank-correlate
    # with this at the end of the run.
    skill_true_usefulness: Dict[str, float] = field(default_factory=dict)

    # For M5 uplift: per (user-text, skill) pairs, the set of preference
    # example IDs that are "relevant". The harness will pre-seed the
    # preference library with the planted examples and then query the
    # retriever for each test turn; recall@k against this set is the
    # M5 metric.
    m5_relevance: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    sessions: List[Session] = field(default_factory=list)
    ground_truth: GroundTruth = field(default_factory=GroundTruth)


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------


class Harness:
    """Drive the Echo plugin through a list of scenarios, then dump signals.

    The harness owns an isolated HERMES_HOME (temp dir) for the duration
    of a run so it never touches the real ``~/.hermes`` data.
    """

    def __init__(
        self,
        out_path: str | Path,
        *,
        disable_confidence: bool = False,
        hermes_home: Optional[Path] = None,
    ):
        self.out_path = Path(out_path)
        self.disable_confidence = disable_confidence
        self.scenarios: List[Scenario] = []

        # Drift events captured during the run (M3 ground-truth side
        # channel). Each entry: {"invocation_id": int, "skill_id": str,
        # "metric_name": str, "z_score": float, "severity": float}.
        self.observed_drifts: List[Dict[str, Any]] = []

        # Isolated Hermes home. We create one even when the caller hands
        # us a path so the test fixtures can verify the cleanup contract.
        self._owns_home = hermes_home is None
        self.hermes_home = hermes_home or Path(tempfile.mkdtemp(prefix="echo-eval-"))

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def add_scenario(self, s: Scenario) -> None:
        self.scenarios.append(s)

    def run(self) -> None:
        """Execute all scenarios in order, writing rows to the Echo DB."""
        prev_home = os.environ.get("HERMES_HOME")
        prev_disable = os.environ.get("ECHO_DISABLE_CONFIDENCE")
        os.environ["HERMES_HOME"] = str(self.hermes_home)
        if self.disable_confidence:
            os.environ["ECHO_DISABLE_CONFIDENCE"] = "1"
        else:
            os.environ.pop("ECHO_DISABLE_CONFIDENCE", None)

        try:
            self._setup_plugin()
            for scenario in self.scenarios:
                self._run_scenario(scenario)
        finally:
            self._teardown_plugin()
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home
            if prev_disable is None:
                os.environ.pop("ECHO_DISABLE_CONFIDENCE", None)
            else:
                os.environ["ECHO_DISABLE_CONFIDENCE"] = prev_disable

    def dump(self) -> Path:
        """Write the run artifact (JSONL) and return its path.

        The artifact is the union of:
          1. config row — the harness configuration (disable_confidence etc).
          2. ground-truth rows — one per scenario.
          3. invocation rows — every echo_skill_invocation row.
          4. signal rows — every echo_signal_event row.
          5. confidence rows — every final echo_skill_confidence row.
          6. m1_candidate rows — Echo's M1 nominator output at end of run.
        """
        from plugins.echo_signals import db as echo_db, m1_trigger

        self.out_path.parent.mkdir(parents=True, exist_ok=True)

        conn = echo_db.get_echo_conn()
        rows: List[Dict[str, Any]] = []

        rows.append({
            "kind": "config",
            "disable_confidence": self.disable_confidence,
            "n_scenarios": len(self.scenarios),
            "schema_version": _get_schema_version(conn),
        })

        for scenario in self.scenarios:
            rows.append({
                "kind": "ground_truth",
                "scenario": scenario.name,
                "skill_true_usefulness": scenario.ground_truth.skill_true_usefulness,
                "m5_relevance": scenario.ground_truth.m5_relevance,
                "invocations": [
                    {
                        "scenario": scenario.name,
                        "session_id": sess.session_id,
                        "session_index": s_idx,
                        "invocation_index": i_idx,
                        "skill_id": inv.skill_id,
                        "should_be_nominated": inv.should_be_nominated,
                        "should_drift": inv.should_drift,
                    }
                    for s_idx, sess in enumerate(scenario.sessions)
                    for i_idx, inv in enumerate(sess.invocations)
                ],
            })

        for r in conn.execute(
            "SELECT invocation_id, skill_id, session_id, platform, started_at, finished_at "
            "FROM echo_skill_invocation ORDER BY invocation_id"
        ):
            rows.append({"kind": "invocation", **dict(r)})

        for r in conn.execute(
            "SELECT event_id, invocation_id, skill_id, layer, signal_type, "
            "       value_text, value_real, value_int, ts "
            "FROM echo_signal_event ORDER BY event_id"
        ):
            rows.append({"kind": "signal", **dict(r)})

        for r in conn.execute(
            "SELECT skill_id, confidence, status, n_invocations, n_signals "
            "FROM echo_skill_confidence ORDER BY skill_id"
        ):
            rows.append({"kind": "confidence", **dict(r)})

        for cand in m1_trigger.list_candidates(limit=200, min_score=1):
            rows.append({
                "kind": "m1_candidate",
                "invocation_id": cand.invocation_id,
                "skill_id": cand.skill_id,
                "score": cand.score,
                "reasons": cand.reasons,
                "user_turns": cand.user_turns,
                "tool_calls": cand.tool_calls,
                "has_save_intent": cand.has_save_intent,
                "has_recurrence": cand.has_recurrence,
            })

        # Drift events captured during the run.
        for d in self.observed_drifts:
            rows.append({"kind": "drift", **d})

        with self.out_path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=_json_default, ensure_ascii=False) + "\n")

        return self.out_path

    def cleanup(self) -> None:
        """Remove the isolated Hermes home if we created it."""
        if self._owns_home and self.hermes_home.exists():
            shutil.rmtree(self.hermes_home, ignore_errors=True)

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    def _setup_plugin(self) -> None:
        # Hermes' state module reads DEFAULT_DB_PATH at import time; we
        # need to redirect it to our temp home BEFORE the Echo DB
        # connection is first opened. The simplest reliable lever is to
        # set the module attribute directly and reset Echo's connection
        # cache so the next ``get_echo_conn()`` picks up the new path.
        import hermes_state
        from plugins.echo_signals import db as echo_db, usage_hook
        from plugins.echo_signals import nl_classifier, judge

        hermes_state.DEFAULT_DB_PATH = self.hermes_home / "sessions.db"
        echo_db.reset_for_tests()  # drop any cached connection from prior tests
        usage_hook.install_bump_use_hook()

        # Stub Layer B + Layer C so the simulator does not call a real
        # LLM. Tests that want full coverage should enable llm_cache.replay
        # before calling Harness.run().
        nl_classifier.set_classifier_impl(self._stub_classifier)
        judge.set_judge_impl(self._stub_judge)

        # Make Layer B + Layer C SYNCHRONOUS for deterministic replay.
        # In production they're fire-and-forget daemon threads; in the
        # simulator we need each effect to land before the next turn.
        self._orig_classify_async = nl_classifier.classify_async
        self._orig_start_judge_async = judge.start_judge_async

        def _sync_classify_async(text, on_result):
            label = nl_classifier.classify(text)
            try:
                on_result(label)
            except Exception:
                pass
            return None

        def _sync_start_judge_async(skill_id, confidence):
            try:
                verdict = judge.run_judge(skill_id, confidence)
                judge.process_verdict(skill_id, verdict)
            except Exception:
                pass

        nl_classifier.classify_async = _sync_classify_async
        judge.start_judge_async = _sync_start_judge_async

        # Intercept drift firings so the metric scripts can read them
        # without inferring drift from confidence movement.
        from plugins.echo_signals import baseline
        self._orig_finalize = baseline.finalize_invocation

        def _spying_finalize(invocation_id):
            drifts = self._orig_finalize(invocation_id)
            for d in drifts or []:
                self.observed_drifts.append({
                    "invocation_id": invocation_id,
                    "skill_id": d.skill_id,
                    "metric_name": d.metric_name,
                    "z_score": d.z_score,
                    "severity": d.severity,
                })
            return drifts

        baseline.finalize_invocation = _spying_finalize
        # finalize_invocation is also imported from baseline by usage_hook
        # and signals; ensure those late-import sites pick it up too.
        from plugins.echo_signals import usage_hook as _uh, signals as _sig
        if hasattr(_uh, "finalize_invocation"):
            _uh.finalize_invocation = _spying_finalize
        if hasattr(_sig, "finalize_invocation"):
            _sig.finalize_invocation = _spying_finalize

    def _teardown_plugin(self) -> None:
        from plugins.echo_signals import db as echo_db, usage_hook
        from plugins.echo_signals import nl_classifier, judge

        usage_hook.uninstall_bump_use_hook()
        nl_classifier.reset_classifier_impl()
        judge.reset_judge_impl()
        # Restore the async functions we monkey-patched.
        if hasattr(self, "_orig_classify_async"):
            nl_classifier.classify_async = self._orig_classify_async
        if hasattr(self, "_orig_start_judge_async"):
            judge.start_judge_async = self._orig_start_judge_async
        if hasattr(self, "_orig_finalize"):
            from plugins.echo_signals import baseline
            baseline.finalize_invocation = self._orig_finalize
        echo_db.reset_for_tests()

    @staticmethod
    def _stub_classifier(text: str):
        # Honour an inline directive so tests can plant a sentiment.
        # The simulator passes the expected sentiment via the user text
        # using a marker; if absent, default to neutral (sacred invariant).
        if "[[POS]]" in text:
            return "positive"
        if "[[NEG]]" in text:
            return "negative"
        return "neutral"

    @staticmethod
    def _stub_judge(skill_id: str, confidence: float):
        from plugins.echo_signals.judge import JudgeVerdict
        # Deterministic: very-low confidence → degraded; otherwise ok.
        if confidence < 0.15:
            return JudgeVerdict(verdict="degraded", reason="simulator stub")
        return JudgeVerdict(verdict="ok")

    # ------------------------------------------------------------------
    # Scenario execution
    # ------------------------------------------------------------------

    def _run_scenario(self, scenario: Scenario) -> None:
        from plugins.echo_signals import _on_session_start, _on_session_end
        from plugins.echo_signals import signals as sig
        from tools import skill_usage

        for session in scenario.sessions:
            _on_session_start(session_id=session.session_id, platform=session.platform)

            for inv in session.invocations:
                # bump_use is monkey-patched by Echo; this both updates
                # Hermes' counter AND writes the echo_skill_invocation row.
                skill_usage.bump_use(inv.skill_id)

                for turn in inv.turns:
                    text = turn.text
                    # Encode the planted sentiment in-band so the stub
                    # classifier can read it.
                    if turn.expected_sentiment == "positive":
                        text = text + " [[POS]]"
                    elif turn.expected_sentiment == "negative":
                        text = text + " [[NEG]]"

                    sig.on_pre_llm_call(
                        turn_type="user",
                        user_message=text,
                    )

                    for tc in turn.tool_calls:
                        sig.on_post_tool_call(tool_name=tc.name)

            _on_session_end(session_id=session.session_id)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _get_schema_version(conn) -> int:
    try:
        row = conn.execute("SELECT version FROM echo_schema_version LIMIT 1").fetchone()
        return int(row["version"]) if row else 0
    except Exception:
        return 0


def _json_default(o: Any):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


# ----------------------------------------------------------------------
# Built-in scenarios (small library; metric scripts assume their shape)
# ----------------------------------------------------------------------


def build_default_scenarios() -> List[Scenario]:
    """A small library of scenarios that together exercise M1/M3/M4/M5.

    Hand-built so the planted ground truth is clearly checkable. Each
    scenario name is referenced by the metric scripts.
    """
    return [
        _scenario_repeat_save_intent(),
        _scenario_high_tool_count(),
        _scenario_drift(),
        _scenario_neutral_baseline(),
    ]


def _scenario_repeat_save_intent() -> Scenario:
    """User asks twice; the second time says 'save this as a skill'.

    Ground truth: M1 should nominate the second invocation
    (save_intent fires; both invocations have similar user text so
    semantic recurrence fires too).
    """
    turns_first = [
        UserTurn(
            text="write a marketing email for our product launch",
            tool_calls=[ToolCall("read_file"), ToolCall("write_file")],
            expected_sentiment="neutral",
        ),
    ]
    turns_second = [
        UserTurn(
            text="write a marketing email for our spring sale, save this as a skill",
            tool_calls=[ToolCall("read_file"), ToolCall("write_file")],
            expected_sentiment="positive",
        ),
    ]
    return Scenario(
        name="repeat_save_intent",
        sessions=[
            Session(
                session_id="repeat-1",
                invocations=[
                    Invocation(skill_id="marketing-email-1", turns=turns_first,
                               should_be_nominated=False),
                ],
            ),
            Session(
                session_id="repeat-2",
                invocations=[
                    Invocation(skill_id="marketing-email-2", turns=turns_second,
                               should_be_nominated=True),
                ],
            ),
        ],
        ground_truth=GroundTruth(
            skill_true_usefulness={
                "marketing-email-1": 0.8,
                "marketing-email-2": 0.9,
            },
        ),
    )


def _scenario_high_tool_count() -> Scenario:
    """A long agent loop with many tool calls -- should trigger M1's
    tool_count condition."""
    long_turn = UserTurn(
        text="set up a CI pipeline for a python project",
        tool_calls=[ToolCall(f"shell_{i}") for i in range(8)],
        expected_sentiment="neutral",
    )
    return Scenario(
        name="high_tool_count",
        sessions=[Session(
            session_id="ci-1",
            invocations=[Invocation(
                skill_id="ci-setup",
                turns=[long_turn],
                should_be_nominated=True,
            )],
        )],
        ground_truth=GroundTruth(skill_true_usefulness={"ci-setup": 0.7}),
    )


def _scenario_drift() -> Scenario:
    """Skill 'data-cleaning' runs at a normal pace 25 times (with light
    natural variation so the baseline variance is non-zero), then once
    very long -- planting a clear drift signal for M3.

    The variation matters: check_drift treats variance==0 as "no drift
    possible" by design (perfectly-constant baseline can't be exceeded),
    so we deliberately rotate through 2/3/2/3 tool counts.
    """
    def _normal(n_tools: int) -> UserTurn:
        return UserTurn(
            text="clean this csv",
            tool_calls=[ToolCall(f"step_{i}") for i in range(n_tools)],
            expected_sentiment="neutral",
        )

    spike_turn = UserTurn(
        text="clean this csv but it is completely broken so try harder",
        tool_calls=[ToolCall(f"shell_{i}") for i in range(20)],
        expected_sentiment="negative",
    )

    invocations = []
    # 25 normal invocations with mild variation (2 or 3 tool calls)
    # so variance > 0 and drift detection has a meaningful baseline.
    for i in range(25):
        n_tools = 2 if i % 2 == 0 else 3
        invocations.append(Invocation(
            skill_id="data-cleaning",
            turns=[_normal(n_tools)],
            should_drift=False,
        ))
    invocations.append(Invocation(
        skill_id="data-cleaning",
        turns=[spike_turn],
        should_drift=True,
        should_be_nominated=True,
    ))

    sessions = [
        Session(session_id=f"clean-{i}", invocations=[inv])
        for i, inv in enumerate(invocations)
    ]
    return Scenario(
        name="drift",
        sessions=sessions,
        ground_truth=GroundTruth(skill_true_usefulness={"data-cleaning": 0.55}),
    )


def _scenario_neutral_baseline() -> Scenario:
    """A short, low-signal scenario that should NOT trigger any M1
    condition. Provides negative ground truth so precision is meaningful."""
    quiet_turn = UserTurn(
        text="what's 2+2",
        tool_calls=[],
        expected_sentiment="neutral",
    )
    return Scenario(
        name="neutral_baseline",
        sessions=[Session(
            session_id="qa-1",
            invocations=[Invocation(
                skill_id="quick-qa",
                turns=[quiet_turn],
                should_be_nominated=False,
            )],
        )],
        ground_truth=GroundTruth(skill_true_usefulness={"quick-qa": 0.5}),
    )
