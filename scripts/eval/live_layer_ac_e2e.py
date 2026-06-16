#!/usr/bin/env python3
"""Live end-to-end smoke for Echo Layer A (behavioral drift) + Layer C (judge).

This script plays a *dissatisfied user* with no human in the loop. It exercises
the parts of Echo that the deterministic eval harness deliberately stubs out —
the real Qwen auxiliary model and the live database:

  Layer A (drift)
    Builds a real behavioral baseline for a throwaway skill (``N_WARM`` "normal"
    invocations with a little spread), then one outlier invocation where the
    user "kept redoing it" (a big ``modification_round_count``). On finalize,
    the real Welford baseline + z-score test fire a genuine ``drift_detected``
    event and the confidence engine takes the hit.

  Layer B (classify + reason score) — used as the *vehicle* for the bad reviews
    Uses the configured Qwen aux model to GENERATE the bad-review texts (this is
    the "LLM mimics the user" part), runs each through the real Layer B sentiment
    classifier (Qwen) AND the real reason scorer (Qwen), and prints what each
    decided — then applies a real thumbs-down confidence cut.

  Layer C (judge)
    The moment the skill crosses ``C_MIN`` into ``pending_review``, the real
    Layer C judge runs — your Qwen model, the 3-vote PRM majority — and its
    verdict is applied (degraded → extra drift; exclusion → scope row).

Everything writes to the LIVE Echo DB (``get_hermes_home()/state.db``), so the
running dashboard reflects it on refresh — you can watch the throwaway skill
appear, its confidence fall, the drift badge land, and the status flip to
"pending review".

HONEST SCOPE: this drives Echo's real signal/baseline/confidence/judge code on
the live DB with the real Qwen model, but it *synthesizes* the skill invocations
rather than spinning up the main agent to actually hold a conversation —
reliably coaxing the agent to invoke one specific skill N times is not
deterministic, and isn't what we're testing here. The valuable "is it really
wired to the live model?" parts (Layer B classify, reason scoring, Layer C
judge) all make real Qwen calls.

It is a MANUAL smoke script, not part of the automated test suite. Run it with
the Hermes venv python so the Qwen aux calls have `openai` available:

    /Users/mac/.hermes/hermes-agent/venv/bin/python -m scripts.eval.live_layer_ac_e2e

Flags:
    --skill-id     throwaway skill name (default: echo-e2e-demo)
    --warm         baseline warm-up invocations (default: baseline.N_WARM)
    --outlier      modification rounds in the outlier invocation (default: 14)
    --reviews      max bad reviews to apply (default: 4; it usually crosses on 1)
    --no-llm-reviews  use canned review texts instead of asking Qwen to write them
    --cleanup      delete this skill's echo_* rows at the end (default: keep,
                   so you can inspect it in the dashboard)
"""

from __future__ import annotations

import argparse
import sys
import time

# --- canned fallbacks (quality complaints, Chinese) -------------------------
# Used with --no-llm-reviews or if the generation call fails. These are
# deliberately QUALITY complaints (not wrong-context), so the judge should
# return "degraded".
CANNED_REVIEWS = [
    "写得太假了，一点都不真实，根本没法用。",
    "质量太差了，还不如我自己写的。",
    "这个结果完全不行，逻辑很混乱。",
    "太敷衍了，细节基本都是错的。",
    "不满意，改了之后还是老样子，没什么改进。",
]


def _rule(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _aux_reachable() -> bool:
    """Tiny real call to confirm the Qwen aux path actually answers."""
    try:
        from agent.auxiliary_client import call_llm

        resp = call_llm(
            task="echo_judge",
            messages=[{"role": "user", "content": "reply with the single word: ok"}],
            max_tokens=5,
            temperature=0.0,
        )
        out = resp.choices[0].message.content
        return isinstance(out, str) and len(out.strip()) > 0
    except Exception as exc:  # noqa: BLE001 — we want the reason printed
        print(f"  [aux unreachable] {type(exc).__name__}: {exc}")
        return False


def _generate_reviews(n: int) -> list[str]:
    """Ask Qwen to write N distinct short Chinese quality complaints."""
    from agent.auxiliary_client import call_llm

    prompt = (
        "你在扮演一个对 AI 助手输出不满意的用户。该助手用某个技能生成了内容，"
        "但质量不行。请写 {n} 条互不相同的简短中文差评，每条不超过 30 字，"
        "都是在抱怨【质量】问题（不够真实/不准确/逻辑乱/敷衍），不要提到换工具或换场景。"
        "每条占一行，不要编号，不要多余说明。"
    ).format(n=n)
    resp = call_llm(
        task="echo_judge",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.7,
    )
    text = resp.choices[0].message.content or ""
    lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()]
    return lines[:n] if lines else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill-id", default="echo-e2e-demo")
    ap.add_argument("--warm", type=int, default=None)
    ap.add_argument("--outlier", type=int, default=14)
    ap.add_argument("--reviews", type=int, default=4)
    ap.add_argument("--no-llm-reviews", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    # Imports after argparse so --help works without a configured Hermes.
    from hermes_constants import display_hermes_home
    from plugins.echo_signals import baseline as bl
    from plugins.echo_signals import confidence as conf
    from plugins.echo_signals import judge as jdg
    from plugins.echo_signals import nl_classifier, reason_scorer
    from plugins.echo_signals import session_context as sc
    from plugins.echo_signals import usage_hook
    from plugins.echo_signals.db import get_echo_conn
    from plugins.echo_signals.signals import record_signal

    skill = args.skill_id
    warm = args.warm if args.warm is not None else bl.N_WARM
    session_id = f"e2e-{int(time.time())}"

    _rule("Echo live E2E — Layer A drift + Layer C judge")
    print(f"  HERMES_HOME     : {display_hermes_home()}")
    print(f"  skill_id        : {skill}")
    print(f"  N_WARM          : {bl.N_WARM}  (warm-up invocations: {warm})")
    print(f"  outlier modif   : {args.outlier} rounds")
    print(f"  BETA_DRIFT      : {conf.BETA_DRIFT}   "
          f"BETA_EXPLICIT_NEGATIVE : {conf.BETA_EXPLICIT_NEGATIVE}")
    print(f"  C_MIN / C_RETIRE: {conf.C_MIN} / {conf.C_RETIRE}")

    # Make sure the schema exists, then start clean for this skill so a re-run
    # is reproducible.
    conn = get_echo_conn()
    _delete_skill_rows(conn, skill)

    print("\n  Checking the Qwen aux model is reachable …")
    if not _aux_reachable():
        print("\n  !! The auxiliary (Qwen) model did not answer. Layer B/C would")
        print("     silently fall back to neutral/ok and the demo would be")
        print("     meaningless. Run this with the Hermes venv python:")
        print("       /Users/mac/.hermes/hermes-agent/venv/bin/python "
              "-m scripts.eval.live_layer_ac_e2e")
        return 2
    print("  aux model OK.")

    sc.set_session_context(session_id, "cli")

    # ----------------------------------------------------------------- Layer A
    _rule("LAYER A — build baseline, then drift on an outlier invocation")
    # A little spread (2,3,2,3,…) so the baseline variance is > 0; a constant
    # series has variance 0 and the z-score test correctly refuses to fire.
    warm_modif = [2 + (i % 2) for i in range(warm)]
    print(f"  {warm} normal invocations, modification rounds = {warm_modif}, "
          f"tool calls = 3 each")
    for c in warm_modif:
        usage_hook._record_invocation(skill)          # creates inv, finalizes prior
        inv = sc.get_current_invocation_id()
        _add_behaviour(record_signal, inv, user_turns=c, tool_calls=3)

    # Outlier: the user kept redoing it. Creating it finalizes the last normal
    # invocation, which pushes the baseline to n == N_WARM (becomes "ready").
    usage_hook._record_invocation(skill)
    outlier_inv = sc.get_current_invocation_id()
    _add_behaviour(record_signal, outlier_inv, user_turns=args.outlier, tool_calls=3)
    print(f"  outlier invocation #{outlier_inv}: "
          f"modification rounds = {args.outlier}, tool calls = 3")

    c_before = _confidence(conn, skill)
    drifts = bl.finalize_invocation(outlier_inv)       # real drift detection
    c_after = _confidence(conn, skill)

    if drifts:
        for d in drifts:
            print(f"  >> DRIFT  metric={d.metric_name}  z={d.z_score:+.2f}  "
                  f"baseline μ={d.baseline_mean:.2f}  severity={d.severity:.2f}")
        print(f"  confidence: {c_before:.3f} -> {c_after:.3f}  "
              f"(status: {_status(conn, skill)})")
        print("  NOTE: tool_call_count stayed flat (variance 0) so only the "
              "high-weight modification-round metric drifted — as designed.")
    else:
        print("  (no drift detected — try a larger --outlier or check N_WARM)")

    # ----------------------------------------------------------------- Layer B/C
    _rule("LAYER B/C — generate bad reviews (Qwen), rate them, trigger the judge")
    if args.no_llm_reviews:
        reviews = CANNED_REVIEWS[: args.reviews]
        print("  using canned review texts (--no-llm-reviews)")
    else:
        print("  asking Qwen to write the bad reviews …")
        reviews = _generate_reviews(args.reviews) or CANNED_REVIEWS[: args.reviews]
    for i, r in enumerate(reviews, 1):
        print(f"    review {i}: {r}")

    judged = False
    for i, text in enumerate(reviews, 1):
        print(f"\n  --- review {i} -------------------------------------------")
        # Layer B sentiment on the SAME text (real Qwen) — shown for insight.
        label = nl_classifier.classify(text)
        print(f"  Layer B sentiment (Qwen) : {label}")

        # Base thumbs-down click (explicit_negative, the dashboard's -1 path).
        before = _confidence(conn, skill)
        res = conf.update_confidence(skill, "explicit_negative")
        record_signal(invocation_id=outlier_inv, layer="B",
                      signal_type="explicit_negative", value_text=text)
        print(f"  thumbs-down              : {before:.3f} -> {res.new_confidence:.3f}"
              f"   [{res.old_status} -> {res.new_status}]")

        # Reason scoring (real Qwen): grade the WORDS, apply a graded same-
        # direction step — exactly what dashboard /feedback does.
        rs = reason_scorer.score_reason("down", skill, text)
        print(f"  reason score (Qwen)      : {rs.score:+d}  «{rs.rationale}»")
        if rs.score != 0:
            ev = "explicit_positive" if rs.score > 0 else "explicit_negative"
            sev = abs(rs.score) / 5.0
            r2 = conf.update_confidence(skill, ev, severity=sev)
            record_signal(invocation_id=outlier_inv, layer="B",
                          signal_type="reason_score",
                          value_real=float(rs.score), value_text=rs.rationale)
            print(f"  graded reason step       : -> {r2.new_confidence:.3f}"
                  f"   [{r2.old_status} -> {r2.new_status}]")

        # Crossing into pending_review → fire the real Layer C judge once.
        if not judged and _status(conn, skill) == conf.STATUS_PENDING_REVIEW:
            judged = True
            _run_judge(jdg, conn, conf, skill)
            break

    if not judged:
        print("\n  Skill never crossed C_MIN after the reviews; no judge run.")
        print(f"  final confidence: {_confidence(conn, skill):.3f}  "
              f"status: {_status(conn, skill)}")

    # ------------------------------------------------------------------- wrap
    _rule("FINAL STATE")
    _dump_timeline(conn, skill)
    print(f"\n  Open the dashboard ( http://127.0.0.1:9119/echo ) and refresh to "
          f"see '{skill}'.")
    if args.cleanup:
        _delete_skill_rows(conn, skill)
        print(f"  --cleanup: removed all echo_* rows for '{skill}'.")
    else:
        print(f"  To remove the throwaway skill later, re-run with --cleanup "
              f"(or it'll just sit in the ranking).")

    sc.clear_session_context()
    return 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _add_behaviour(record_signal, invocation_id: int, *, user_turns: int,
                   tool_calls: int) -> None:
    for _ in range(user_turns):
        record_signal(invocation_id=invocation_id, layer="A",
                      signal_type="user_turn")
    for _ in range(tool_calls):
        record_signal(invocation_id=invocation_id, layer="A",
                      signal_type="tool_call", value_text="some_tool")


def _confidence(conn, skill: str) -> float:
    row = conn.execute(
        "SELECT confidence FROM echo_skill_confidence WHERE skill_id = ?",
        (skill,),
    ).fetchone()
    return float(row["confidence"]) if row else float("nan")


def _status(conn, skill: str) -> str:
    row = conn.execute(
        "SELECT status FROM echo_skill_confidence WHERE skill_id = ?",
        (skill,),
    ).fetchone()
    return row["status"] if row else "?"


def _run_judge(jdg, conn, conf, skill: str) -> None:
    print("\n  >> CROSSED C_MIN — skill is now pending_review. Running Layer C "
          "judge (Qwen, 3-vote PRM) …")
    c = _confidence(conn, skill)
    verdict = jdg.run_judge(skill, c)               # real Qwen, majority vote
    print(f"  judge verdict            : {verdict.verdict}")
    if verdict.reason:
        print(f"  judge reason             : {verdict.reason}")
    if verdict.context:
        print(f"  judge exclusion context  : {verdict.context}")
    jdg.process_verdict(skill, verdict)             # apply it
    print(f"  after verdict            : confidence={_confidence(conn, skill):.3f}"
          f"   status={_status(conn, skill)}")
    if verdict.verdict == "exclusion":
        row = conn.execute(
            "SELECT exclusion_conditions FROM echo_skill_scope WHERE skill_id = ?",
            (skill,),
        ).fetchone()
        if row:
            print(f"  exclusion_conditions     : {row['exclusion_conditions']}")


def _dump_timeline(conn, skill: str) -> None:
    print(f"  confidence={_confidence(conn, skill):.3f}  "
          f"status={_status(conn, skill)}")
    rows = conn.execute(
        "SELECT signal_type, COUNT(*) n FROM echo_signal_event "
        "WHERE skill_id = ? GROUP BY signal_type ORDER BY signal_type",
        (skill,),
    ).fetchall()
    print("  signal events:")
    for r in rows:
        print(f"    {r['signal_type']:<22} {r['n']}")


def _delete_skill_rows(conn, skill: str) -> None:
    for tbl in (
        "echo_signal_event",
        "echo_skill_invocation",
        "echo_skill_baseline",
        "echo_skill_scope",
        "echo_skill_confidence",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE skill_id = ?", (skill,))
        except Exception:
            pass
    conn.commit()


if __name__ == "__main__":
    sys.exit(main())
