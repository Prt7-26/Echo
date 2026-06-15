"""Unit tests for skill_dedup — the M1 new-skill dedup check.

Covers: skill-library enumeration, tolerant JSON parsing + unknown-name
guard, the aux_config gate, and fail-soft behavior.
"""

from __future__ import annotations

import pytest

from plugins.echo_signals import skill_dedup as sd


@pytest.fixture(autouse=True)
def _reset_impl():
    sd.reset_dedup_impl()
    yield
    sd.reset_dedup_impl()


# ---------------------------------------------------------------------------
# enumerate_skills
# ---------------------------------------------------------------------------


class TestEnumerateSkills:
    def _write_skill(self, root, name, desc):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\nbody\n",
            encoding="utf-8",
        )

    def test_reads_name_and_description(self, tmp_path, monkeypatch):
        self._write_skill(tmp_path, "email-draft", "Draft marketing emails")
        self._write_skill(tmp_path, "ascii-art", "Render ASCII visual art")
        monkeypatch.setattr(sd, "enumerate_skills", sd.enumerate_skills)
        from agent import skill_utils
        monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [tmp_path])

        skills = sd.enumerate_skills()
        names = {s.name for s in skills}
        assert names == {"email-draft", "ascii-art"}
        by_name = {s.name: s.description for s in skills}
        assert "marketing" in by_name["email-draft"]

    def test_empty_library(self, tmp_path, monkeypatch):
        from agent import skill_utils
        monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [tmp_path])
        assert sd.enumerate_skills() == []

    def test_unreadable_skill_skipped(self, tmp_path, monkeypatch):
        self._write_skill(tmp_path, "good", "fine skill")
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
        from agent import skill_utils
        monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [tmp_path])
        names = {s.name for s in sd.enumerate_skills()}
        assert "good" in names  # the valid one survives


# ---------------------------------------------------------------------------
# _parse_dedup
# ---------------------------------------------------------------------------


class TestParseDedup:
    VALID = {"email-draft", "ascii-art"}

    def test_clean_match(self):
        r = sd._parse_dedup('{"match": "email-draft", "reason": "same job"}', self.VALID)
        assert r.match == "email-draft"
        assert r.reason == "same job"

    def test_null_match(self):
        assert sd._parse_dedup('{"match": null}', self.VALID).match is None

    def test_code_fence_wrapped(self):
        text = '```json\n{"match": "ascii-art"}\n```'
        assert sd._parse_dedup(text, self.VALID).match == "ascii-art"

    def test_prose_padded(self):
        text = 'Sure! Here is my answer: {"match": "email-draft"} hope it helps'
        assert sd._parse_dedup(text, self.VALID).match == "email-draft"

    def test_unknown_name_rejected(self):
        # LLM hallucinates a skill that isn't in the library.
        r = sd._parse_dedup('{"match": "totally-made-up"}', self.VALID)
        assert r.match is None

    def test_garbage_is_no_match(self):
        assert sd._parse_dedup("not json at all", self.VALID).match is None
        assert sd._parse_dedup("", self.VALID).match is None


# ---------------------------------------------------------------------------
# check_duplicate — gate + injection + fail-soft
# ---------------------------------------------------------------------------


class TestCheckDuplicate:
    def test_off_when_aux_disabled(self, monkeypatch):
        from plugins.echo_signals import aux_config
        monkeypatch.setattr(aux_config, "aux_enabled_for", lambda task: False)
        # Impl must not even be consulted.
        sd.set_dedup_impl(lambda t, s: (_ for _ in ()).throw(AssertionError("called")))
        assert sd.check_duplicate("draft an email").match is None

    def test_empty_text_no_match(self, monkeypatch):
        from plugins.echo_signals import aux_config
        monkeypatch.setattr(aux_config, "aux_enabled_for", lambda task: True)
        assert sd.check_duplicate("   ").match is None

    def test_injected_match(self, monkeypatch):
        from plugins.echo_signals import aux_config
        monkeypatch.setattr(aux_config, "aux_enabled_for", lambda task: True)
        monkeypatch.setattr(sd, "enumerate_skills",
                            lambda: [sd.SkillInfo("email-draft", "draft emails")])
        sd.set_dedup_impl(lambda t, s: sd.DedupResult(match="email-draft", reason="overlap"))
        r = sd.check_duplicate("write me a launch email")
        assert r.match == "email-draft"

    def test_empty_library_no_match(self, monkeypatch):
        from plugins.echo_signals import aux_config
        monkeypatch.setattr(aux_config, "aux_enabled_for", lambda task: True)
        monkeypatch.setattr(sd, "enumerate_skills", lambda: [])
        sd.set_dedup_impl(lambda t, s: (_ for _ in ()).throw(AssertionError("called")))
        assert sd.check_duplicate("anything").match is None

    def test_impl_exception_is_fail_soft(self, monkeypatch):
        from plugins.echo_signals import aux_config
        monkeypatch.setattr(aux_config, "aux_enabled_for", lambda task: True)
        monkeypatch.setattr(sd, "enumerate_skills",
                            lambda: [sd.SkillInfo("x", "y")])
        sd.set_dedup_impl(lambda t, s: (_ for _ in ()).throw(RuntimeError("boom")))
        assert sd.check_duplicate("anything").match is None
