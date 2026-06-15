"""Shared fixtures for Echo signal tests.

Default behavior: replace ``judge.start_judge_async`` with a no-op so
any test that incidentally pushes a skill into ``pending_review``
(drift detection, NL classifier, dashboard feedback) doesn't spawn a
real daemon thread attempting an LLM call. This keeps tests
deterministic and prevents thread-leak-induced suite-teardown hangs
(see pyproject.toml's pytest-timeout note for the underlying issue).

Opt-out: tests that exercise the judge lifecycle proper add the
``real_judge`` fixture to their signature; the stub is then bypassed
for that test. ``test_judge.py::TestStartJudgeAsync`` uses this.
"""

from __future__ import annotations

import threading

import pytest


@pytest.fixture
def real_judge():
    """Marker fixture — request this to bypass the autouse stub below.

    Empty body; the stub fixture detects the request via the
    ``request.fixturenames`` set.
    """
    return None


@pytest.fixture
def real_reason_scorer():
    """Marker fixture — request this to bypass the reason_scorer stub below."""
    return None


@pytest.fixture
def real_nomination():
    """Marker fixture — request this to bypass the nomination-async stub below."""
    return None


@pytest.fixture(autouse=True)
def _stub_judge_async(monkeypatch, request):
    """Stub ``judge.start_judge_async`` unless the test requested
    ``real_judge``."""
    if "real_judge" in request.fixturenames:
        yield
        return

    from plugins.echo_signals import judge as jdg

    def _noop(*_a, **_kw):
        return threading.Thread(target=lambda: None)

    monkeypatch.setattr(jdg, "start_judge_async", _noop)
    yield


@pytest.fixture(autouse=True)
def _stub_reason_scorer_async(monkeypatch, request):
    """Stub ``reason_scorer.score_reason_async`` unless the test requested
    ``real_reason_scorer`` — so the dashboard /feedback path doesn't spawn a
    real daemon thread attempting an aux-LLM call when a reason is supplied."""
    if "real_reason_scorer" in request.fixturenames:
        yield
        return

    from plugins.echo_signals import reason_scorer as rs

    def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(rs, "score_reason_async", _noop)
    yield


@pytest.fixture(autouse=True)
def _stub_nomination_async(monkeypatch, request):
    """Stub ``m1_nomination._start_dedup_async`` unless the test requested
    ``real_nomination`` — so a skill-less turn crossing the M1 threshold
    doesn't spawn a real daemon thread hitting the dedup aux-LLM at test time."""
    if "real_nomination" in request.fixturenames:
        yield
        return

    from plugins.echo_signals import m1_nomination as nom

    def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(nom, "_start_dedup_async", _noop)
    yield
