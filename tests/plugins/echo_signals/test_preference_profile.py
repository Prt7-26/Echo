"""Tests for the M5 consolidated preference profile (schema v11)."""

from __future__ import annotations

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import preference_rag as prag


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


class TestClauseSplit:
    def test_splits_and_strips_scaffolding(self):
        cl = prag._split_clauses(
            "Rule 1 violated: always end with 'Onward, R.'; keep under 60 words. "
            "Next time: never use exclamation marks")
        joined = " | ".join(cl)
        assert "always end with 'Onward, R.'" in joined
        assert "keep under 60 words" in joined
        assert "never use exclamation marks" in joined
        # the "Rule 1 violated:" / "Next time:" scaffolding is stripped
        assert not any("violated" in c.lower() for c in cl)
        assert not any(c.lower().startswith("next time") for c in cl)

    def test_drops_tiny_fragments(self):
        assert prag._split_clauses("ok. no. yes") == []


class TestAddAndGet:
    def test_add_and_retrieve_global(self, isolated_db):
        echo_db.get_echo_conn()
        n = prag.add_preference_clauses("use British spelling; never use em-dashes")
        assert n == 2
        clauses = prag.get_profile_clauses()
        assert any("British spelling" in c for c in clauses)
        assert any("em-dashes" in c for c in clauses)

    def test_dedup_idempotent(self, isolated_db):
        echo_db.get_echo_conn()
        prag.add_preference_clauses("keep it under 60 words")
        added_again = prag.add_preference_clauses("Keep it under 60 words.")  # same rule
        assert added_again == 0
        assert len(prag.get_profile_clauses()) == 1

    def test_skill_scoped_vs_global(self, isolated_db):
        echo_db.get_echo_conn()
        prag.add_preference_clauses("always sign off warmly", skill_id="email")
        prag.add_preference_clauses("use British spelling")  # global
        # email skill sees both its own + global
        email = prag.get_profile_clauses("email")
        assert any("sign off warmly" in c for c in email)
        assert any("British spelling" in c for c in email)
        # a different skill sees only the global one
        other = prag.get_profile_clauses("summary")
        assert any("British spelling" in c for c in other)
        assert not any("sign off warmly" in c for c in other)
        # no skill → only global
        glob = prag.get_profile_clauses()
        assert not any("sign off warmly" in c for c in glob)

    def test_empty_is_noop(self, isolated_db):
        echo_db.get_echo_conn()
        assert prag.add_preference_clauses("") == 0
        assert prag.add_preference_clauses("   ") == 0
        assert prag.get_profile_clauses() == []


class TestInjection:
    def test_inject_includes_profile(self, isolated_db):
        echo_db.get_echo_conn()
        prag.add_preference_clauses("always end with 'Onward, R.'")
        out = prag.on_pre_llm_call_inject(user_message="Write an email to a client")
        assert out is not None
        assert "Onward, R." in out["context"]
        assert "standing preferences" in out["context"].lower()

    def test_no_profile_no_injection_from_profile(self, isolated_db):
        echo_db.get_echo_conn()
        # nothing stored → profile contributes nothing (may still return None)
        out = prag.on_pre_llm_call_inject(user_message="Write an email")
        if out is not None:
            assert "standing preferences" not in out["context"].lower()
