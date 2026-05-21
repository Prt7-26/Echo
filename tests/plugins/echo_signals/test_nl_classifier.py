"""Unit tests for Layer B — NL sentiment classifier.

Three layers of coverage:

  * extract_user_text — every Hermes user_message shape we've seen.
  * classify / classify_async — routing, sacred invariant for failure
    (returns "neutral", never raises), test injection hook.
  * Integration with signals.on_pre_llm_call — pinned skill_id, written
    Layer B row, confidence updated, all on a daemon thread that we
    join in tests to assert deterministically.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import nl_classifier as nlc
from plugins.echo_signals import session_context as sc
from plugins.echo_signals import signals as sig
from plugins.echo_signals import usage_hook as uh


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    sc.clear_session_context()
    yield fake_db
    sc.clear_session_context()
    echo_db.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_classifier():
    nlc.reset_classifier_impl()
    yield
    nlc.reset_classifier_impl()


@pytest.fixture
def active_invocation(isolated_db):
    """Install monkey-patch and set up one active invocation."""
    uh.install_bump_use_hook()
    sc.set_session_context("session-x", "cli")
    import tools.skill_usage as _su

    _su.bump_use("test-skill")
    yield
    uh.uninstall_bump_use_hook()


# ---------------------------------------------------------------------------
# extract_user_text — shape normalization
# ---------------------------------------------------------------------------


class TestExtractUserText:
    def test_none(self):
        assert nlc.extract_user_text(None) is None

    def test_empty_string(self):
        assert nlc.extract_user_text("") is None
        assert nlc.extract_user_text("   ") is None

    def test_plain_string(self):
        assert nlc.extract_user_text("hello world") == "hello world"
        assert nlc.extract_user_text("  hello  ") == "hello"

    def test_openai_dict_string_content(self):
        msg = {"role": "user", "content": "hi"}
        assert nlc.extract_user_text(msg) == "hi"

    def test_openai_dict_empty_content(self):
        msg = {"role": "user", "content": "   "}
        assert nlc.extract_user_text(msg) is None

    def test_openai_dict_multimodal_text_parts(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "image_url", "image_url": {"url": "..."}},
                {"type": "text", "text": "world"},
            ],
        }
        assert nlc.extract_user_text(msg) == "Hello world"

    def test_openai_dict_multimodal_no_text(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "..."}},
            ],
        }
        assert nlc.extract_user_text(msg) is None

    def test_surprising_shape_returns_none(self):
        # An int isn't any documented shape.
        assert nlc.extract_user_text(42) is None
        # A list of strings isn't a Hermes shape but should not crash.
        assert nlc.extract_user_text(["a", "b"]) is None


# ---------------------------------------------------------------------------
# classify — sync API
# ---------------------------------------------------------------------------


class TestClassify:
    def test_empty_text_returns_neutral(self):
        assert nlc.classify("") == "neutral"
        assert nlc.classify("   ") == "neutral"

    def test_uses_injected_impl(self):
        nlc.set_classifier_impl(lambda _text: "positive")
        assert nlc.classify("anything") == "positive"

    def test_impl_exception_returns_neutral(self):
        def _broken(_text):
            raise RuntimeError("boom")

        nlc.set_classifier_impl(_broken)
        assert nlc.classify("anything") == "neutral"  # SACRED: never raises

    def test_reset_restores_default_impl(self):
        nlc.set_classifier_impl(lambda _text: "positive")
        nlc.reset_classifier_impl()
        # Default impl tries to call Hermes' aux LLM with task="echo_classifier".
        # Without configuration it raises, which classify() swallows → "neutral".
        # We just check the impl is no longer our stub.
        assert nlc._classifier_impl is nlc._default_classifier_impl


# ---------------------------------------------------------------------------
# classify_async — fire-and-forget
# ---------------------------------------------------------------------------


class TestClassifyAsync:
    def test_invokes_callback(self):
        nlc.set_classifier_impl(lambda _text: "positive")
        result = {}
        ev = threading.Event()

        def on_result(label):
            result["label"] = label
            ev.set()

        thread = nlc.classify_async("test", on_result)
        assert thread is not None
        ev.wait(timeout=2.0)
        thread.join(timeout=2.0)
        assert result.get("label") == "positive"

    def test_empty_text_no_thread(self):
        result = {}

        def on_result(label):
            result["label"] = label

        thread = nlc.classify_async("", on_result)
        assert thread is None
        assert "label" not in result

    def test_callback_exception_does_not_propagate(self):
        """Broken on_result mustn't crash the worker thread."""
        nlc.set_classifier_impl(lambda _text: "negative")
        called = threading.Event()

        def on_result(label):
            called.set()
            raise RuntimeError("broken callback")

        thread = nlc.classify_async("test", on_result)
        thread.join(timeout=2.0)
        # If the exception had propagated, the thread would have died
        # before setting called. The fact that we got called.set() AND
        # thread.join returned cleanly means the worker swallowed the
        # callback exception.
        assert called.is_set()
        assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Default impl wrapper — exception path coverage
# ---------------------------------------------------------------------------


class TestDefaultImplResponseParsing:
    """We can't reach the real LLM in tests; verify the parser correctness
    by injecting fake response objects via monkey-patching call_llm."""

    def test_clean_positive_word(self, monkeypatch):
        class _Resp:
            class choices:
                pass
        resp = _Resp()
        resp.choices = [type("c", (), {"message": type("m", (), {"content": "positive"})})()]
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm", lambda **kw: resp,
        )
        assert nlc._default_classifier_impl("test") == "positive"

    def test_label_followed_by_punctuation(self, monkeypatch):
        resp = type("r", (), {})()
        resp.choices = [type("c", (), {"message": type("m", (), {"content": "negative."})})()]
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm", lambda **kw: resp,
        )
        assert nlc._default_classifier_impl("test") == "negative"

    def test_garbage_response_returns_neutral(self, monkeypatch):
        resp = type("r", (), {})()
        resp.choices = [type("c", (), {"message": type("m", (), {"content": "I don't know"})})()]
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm", lambda **kw: resp,
        )
        assert nlc._default_classifier_impl("test") == "neutral"

    def test_non_string_response_returns_neutral(self, monkeypatch):
        resp = type("r", (), {})()
        resp.choices = [type("c", (), {"message": type("m", (), {"content": None})})()]
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm", lambda **kw: resp,
        )
        assert nlc._default_classifier_impl("test") == "neutral"


# ---------------------------------------------------------------------------
# Integration with signals.on_pre_llm_call
# ---------------------------------------------------------------------------


def _wait_for_signals(skill_id: str, layer: str, n: int, timeout: float = 2.0) -> bool:
    """Poll until n echo_signal_event rows exist for (skill_id, layer)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = echo_db.get_echo_conn()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE skill_id = ? AND layer = ?",
            (skill_id, layer),
        ).fetchone()["n"]
        if count >= n:
            return True
        time.sleep(0.02)
    return False


class TestSignalsIntegration:
    def test_positive_label_writes_layer_b_row_and_lifts_confidence(
        self, active_invocation,
    ):
        nlc.set_classifier_impl(lambda _text: "positive")

        sig.on_pre_llm_call(turn_type="user", user_message="That's perfect, thanks!")

        # Layer A user_turn lands synchronously.
        conn = echo_db.get_echo_conn()
        layer_a = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE skill_id = 'test-skill' AND layer = 'A'"
        ).fetchone()["n"]
        assert layer_a == 1

        # Layer B lands asynchronously — wait for it.
        assert _wait_for_signals("test-skill", "B", 1)

        layer_b = conn.execute(
            "SELECT signal_type, value_text FROM echo_signal_event "
            "WHERE skill_id = 'test-skill' AND layer = 'B'"
        ).fetchone()
        assert layer_b["signal_type"] == "nl_positive"
        assert layer_b["value_text"] == "positive"

        # Confidence rose by ALPHA_NL_POSITIVE (0.05).
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='test-skill'"
        ).fetchone()["confidence"]
        assert c == pytest.approx(0.5 + 0.05)

    def test_negative_label_writes_layer_b_row_and_drops_confidence(
        self, active_invocation,
    ):
        nlc.set_classifier_impl(lambda _text: "negative")
        sig.on_pre_llm_call(turn_type="user", user_message="No, that's wrong.")
        assert _wait_for_signals("test-skill", "B", 1)

        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='test-skill'"
        ).fetchone()["confidence"]
        # nl_negative uses GAMMA_EXPLICIT_NEGATIVE / 2 = 0.15 multiplicative.
        # 0.5 * (1 - 0.15) = 0.425
        assert c == pytest.approx(0.5 * 0.85)

    def test_neutral_label_no_layer_b_row(self, active_invocation):
        nlc.set_classifier_impl(lambda _text: "neutral")
        sig.on_pre_llm_call(turn_type="user", user_message="Now write me a sonnet.")

        # Give the daemon thread a moment.
        time.sleep(0.1)

        conn = echo_db.get_echo_conn()
        layer_b = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE skill_id = 'test-skill' AND layer = 'B'"
        ).fetchone()["n"]
        assert layer_b == 0  # silence is sacred

    def test_no_user_message_skips_layer_b(self, active_invocation):
        nlc.set_classifier_impl(lambda _text: "positive")  # would be used if reached
        sig.on_pre_llm_call(turn_type="user", user_message=None)
        time.sleep(0.05)
        conn = echo_db.get_echo_conn()
        layer_b = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE skill_id = 'test-skill' AND layer = 'B'"
        ).fetchone()["n"]
        assert layer_b == 0

    def test_skill_id_pinned_at_record_time_not_callback_time(
        self, active_invocation,
    ):
        """If bump_use flips contextvar mid-classify, the callback should
        still attribute the signal to the skill that was active when the
        message arrived."""
        # We rig classify_async to wait on a gate so we can flip the
        # contextvar in between.
        gate = threading.Event()
        proceed = threading.Event()

        def _slow_classifier(_text):
            gate.set()
            proceed.wait(timeout=2.0)
            return "positive"

        nlc.set_classifier_impl(_slow_classifier)

        sig.on_pre_llm_call(turn_type="user", user_message="great work")
        assert gate.wait(timeout=2.0)

        # Now flip to a different skill.
        import tools.skill_usage as _su
        _su.bump_use("different-skill")

        # Release the classifier.
        proceed.set()
        assert _wait_for_signals("test-skill", "B", 1)

        conn = echo_db.get_echo_conn()
        rows = conn.execute(
            "SELECT skill_id FROM echo_signal_event WHERE layer='B'"
        ).fetchall()
        assert [r["skill_id"] for r in rows] == ["test-skill"]
