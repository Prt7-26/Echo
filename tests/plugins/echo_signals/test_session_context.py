"""Unit tests for plugins.echo_signals.session_context."""

from __future__ import annotations

import asyncio

import pytest

from plugins.echo_signals import session_context as sc


@pytest.fixture(autouse=True)
def _reset_context():
    """Make sure every test starts from the contextvar defaults."""
    sc.clear_session_context()
    yield
    sc.clear_session_context()


class TestSetGet:
    def test_defaults(self):
        assert sc.get_session_id() is None
        assert sc.get_platform() == "unknown"

    def test_set_then_get(self):
        sc.set_session_context("abc-123", "telegram")
        assert sc.get_session_id() == "abc-123"
        assert sc.get_platform() == "telegram"

    def test_clear_resets(self):
        sc.set_session_context("abc-123", "cli")
        sc.clear_session_context()
        assert sc.get_session_id() is None
        assert sc.get_platform() == "unknown"

    def test_none_platform_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_PLATFORM", "discord")
        sc.set_session_context("s", None)
        assert sc.get_platform() == "discord"

    def test_none_platform_no_env_uses_unknown(self, monkeypatch):
        monkeypatch.delenv("HERMES_PLATFORM", raising=False)
        sc.set_session_context("s", None)
        assert sc.get_platform() == "unknown"


class TestAsyncIsolation:
    """contextvars must propagate correctly into asyncio tasks."""

    def test_context_propagates_into_task(self):
        sc.set_session_context("outer", "cli")

        async def inner():
            return sc.get_session_id(), sc.get_platform()

        result = asyncio.run(inner())
        assert result == ("outer", "cli")

    def test_task_modification_does_not_leak_to_parent(self):
        # Spawning a new asyncio.run creates a fresh Context copy. Mutating
        # within that copy does NOT propagate back to the caller.
        sc.set_session_context("parent", "cli")

        async def inner():
            sc.set_session_context("child", "telegram")

        asyncio.run(inner())
        assert sc.get_session_id() == "parent"
        assert sc.get_platform() == "cli"
