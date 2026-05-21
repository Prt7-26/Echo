#!/usr/bin/env python3
"""End-to-end smoke test for Echo plugin against real Hermes runtime bits.

What this verifies (beyond what unit tests already cover):
  1. Hermes' plugin manager discovers plugins/echo_signals/ via its
     plugin.yaml manifest (not just imports it directly).
  2. register(ctx) installs the bump_use monkey-patch against the REAL
     tools.skill_usage module, not a test double.
  3. A simulated lifecycle (session_start → bump_use → user turns →
     tool calls → session_end) writes the expected rows into a REAL
     hermes_state.SessionDB instance (no mocks of SessionDB).
  4. The confidence engine can read/write rows in that DB.
  5. Aggregated queries used by future M4 logic (e.g. COUNT(user_turn))
     produce sensible numbers.

Run:
    python3 scripts/verify_echo.py

The script uses a temp HERMES_HOME so it doesn't touch your real
~/.hermes data. Cleans up automatically.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# ── 1. Redirect HERMES_HOME *before* importing anything Hermes ────────────
TMP_HOME = Path(tempfile.mkdtemp(prefix="echo-verify-"))
os.environ["HERMES_HOME"] = str(TMP_HOME)

# Add repo root to sys.path so `import plugins.echo_signals` works.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
INFO = "\033[36m·\033[0m"


def section(title: str) -> None:
    print(f"\n\033[1m── {title} ──\033[0m")


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = PASS if ok else FAIL
    print(f"  {mark} {label}{('  ' + detail) if detail else ''}")
    return ok


def info(msg: str) -> None:
    print(f"  {INFO} {msg}")


total_checks = 0
total_passed = 0


def expect(label: str, ok: bool, detail: str = "") -> None:
    global total_checks, total_passed
    total_checks += 1
    if check(label, ok, detail):
        total_passed += 1


try:
    print(f"\033[1mEcho end-to-end smoke test\033[0m")
    print(f"HERMES_HOME = {TMP_HOME}")

    # ─────────────────────────────────────────────────────────────────────
    section("1. Plugin manifest is well-formed")
    # ─────────────────────────────────────────────────────────────────────

    import yaml

    manifest_path = REPO_ROOT / "plugins" / "echo_signals" / "plugin.yaml"
    expect("plugin.yaml exists", manifest_path.is_file())

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    expect("manifest has name field", isinstance(manifest.get("name"), str))
    expect(
        "manifest declares all four runtime hooks",
        set(manifest.get("hooks", [])) == {
            "on_session_start",
            "on_session_end",
            "pre_llm_call",
            "post_tool_call",
        },
        detail=f"actual: {manifest.get('hooks')}",
    )

    # ─────────────────────────────────────────────────────────────────────
    section("2. Hermes plugin manager discovers echo_signals")
    # ─────────────────────────────────────────────────────────────────────

    from hermes_cli import plugins as hermes_plugins

    pm = hermes_plugins.get_plugin_manager()
    # Standalone plugins are opt-in via `plugins.enabled` config; we
    # bypass that and check the lower-level scan output directly. This
    # is the same call discover_and_load() uses internally.
    scanned = pm._scan_directory(  # type: ignore[attr-defined]
        hermes_plugins.get_bundled_plugins_dir(),
        source="bundled",
        skip_names={"memory", "context_engine", "platforms", "model-providers"},
    )
    keys = sorted(m.key or m.name for m in scanned)
    info(f"scanned {len(scanned)} manifests from bundled/")
    found_echo = next(
        (m for m in scanned if (m.key or m.name) == "echo_signals"), None
    )
    expect(
        "echo_signals appears among scanned manifests",
        found_echo is not None,
        detail=f"sample keys: {keys[:8]}…",
    )
    if found_echo is not None:
        expect(
            "echo_signals manifest is parsed as standalone kind",
            found_echo.kind == "standalone",
            detail=f"kind={found_echo.kind}",
        )

    # ─────────────────────────────────────────────────────────────────────
    section("3. register(ctx) installs monkey-patch on real tools.skill_usage")
    # ─────────────────────────────────────────────────────────────────────

    import tools.skill_usage as _su
    from plugins.echo_signals import register as echo_register

    original_bump_use = _su.bump_use

    class FakeCtx:
        def __init__(self):
            self.hooks = {}

        def register_hook(self, name, callback):
            self.hooks.setdefault(name, []).append(callback)

    ctx = FakeCtx()
    echo_register(ctx)

    expect(
        "bump_use is now wrapped",
        getattr(_su.bump_use, "_echo_wrapped", False) is True,
    )
    expect(
        "ctx received all 4 hooks",
        set(ctx.hooks.keys()) == {
            "on_session_start",
            "on_session_end",
            "pre_llm_call",
            "post_tool_call",
        },
        detail=f"got: {sorted(ctx.hooks.keys())}",
    )

    # ─────────────────────────────────────────────────────────────────────
    section("4. Simulated session writes to real state.db")
    # ─────────────────────────────────────────────────────────────────────

    from plugins.echo_signals import db as echo_db
    from plugins.echo_signals import session_context as sc
    from plugins.echo_signals import signals as sig

    # Important: SessionDB needs to exist so state.db is initialized with
    # Hermes' own tables (sessions, messages, …). Echo will lazily add
    # its echo_* tables on first hook fire.
    from hermes_state import SessionDB

    real_db = SessionDB()  # uses HERMES_HOME = TMP_HOME
    info(f"SessionDB opened: {real_db.db_path}")

    # ── Simulate a session ─────────────────────────────────────────────
    # 4a. session_start
    for cb in ctx.hooks["on_session_start"]:
        cb(session_id="verify-session-1", platform="cli")
    expect(
        "session_context populated by on_session_start",
        sc.get_session_id() == "verify-session-1"
        and sc.get_platform() == "cli",
    )

    # 4b. bump_use — through Hermes' real module (which we monkey-patched)
    _su.bump_use("verify-skill")

    info(f"current invocation_id contextvar: {sc.get_current_invocation_id()}")
    expect(
        "current_invocation_id set after bump_use",
        sc.get_current_invocation_id() is not None,
    )

    # 4c. three user turns
    for _ in range(3):
        for cb in ctx.hooks["pre_llm_call"]:
            cb(turn_type="user", api_call_count=0)

    # 4d. a couple of internal / assistant pre_llm_call noise we should ignore
    for cb in ctx.hooks["pre_llm_call"]:
        cb(turn_type="assistant")
        cb(turn_type="tool")

    # 4e. two tool calls
    for tool in ("send_email", "read_file"):
        for cb in ctx.hooks["post_tool_call"]:
            cb(tool_name=tool, args={}, result="", task_id="t", session_id="verify-session-1")

    # 4f. session end
    for cb in ctx.hooks["on_session_end"]:
        cb()
    expect(
        "session_context cleared by on_session_end",
        sc.get_session_id() is None
        and sc.get_current_invocation_id() is None,
    )

    # ── Verify the writes ──────────────────────────────────────────────
    conn = echo_db.get_echo_conn()

    inv = conn.execute(
        "SELECT skill_id, session_id, platform FROM echo_skill_invocation"
    ).fetchone()
    expect(
        "echo_skill_invocation has the verify-skill row",
        inv is not None
        and inv["skill_id"] == "verify-skill"
        and inv["session_id"] == "verify-session-1"
        and inv["platform"] == "cli",
        detail=f"row: {dict(inv) if inv else None}",
    )

    confidence_row = conn.execute(
        "SELECT confidence, n_invocations, n_signals "
        "FROM echo_skill_confidence WHERE skill_id = 'verify-skill'"
    ).fetchone()
    expect(
        "echo_skill_confidence anchor created",
        confidence_row is not None
        and confidence_row["confidence"] == 0.5
        and confidence_row["n_invocations"] == 1,
        detail=f"row: {dict(confidence_row) if confidence_row else None}",
    )

    user_turns = conn.execute(
        "SELECT COUNT(*) AS n FROM echo_signal_event "
        "WHERE signal_type = 'user_turn' AND skill_id = 'verify-skill'"
    ).fetchone()["n"]
    expect(
        "3 user_turn signals (internal noise ignored)",
        user_turns == 3,
        detail=f"got: {user_turns}",
    )

    tool_calls = conn.execute(
        "SELECT value_text FROM echo_signal_event "
        "WHERE signal_type = 'tool_call' ORDER BY event_id"
    ).fetchall()
    tool_names = [r["value_text"] for r in tool_calls]
    expect(
        "2 tool_call signals with correct tool names",
        tool_names == ["send_email", "read_file"],
        detail=f"got: {tool_names}",
    )

    n_session_ended = conn.execute(
        "SELECT COUNT(*) AS n FROM echo_signal_event "
        "WHERE signal_type = 'session_ended' AND skill_id = 'verify-skill'"
    ).fetchone()["n"]
    expect(
        "1 session_ended signal",
        n_session_ended == 1,
        detail=f"got: {n_session_ended}",
    )

    # n_signals = 3 + 2 + 1 = 6
    expect(
        "n_signals on confidence = 6",
        confidence_row is not None
        # Re-read because we updated after the original SELECT.
        and conn.execute(
            "SELECT n_signals FROM echo_skill_confidence WHERE skill_id='verify-skill'"
        ).fetchone()["n_signals"] == 6,
    )

    # ─────────────────────────────────────────────────────────────────────
    section("5. Confidence engine reads/writes the real rows")
    # ─────────────────────────────────────────────────────────────────────

    from plugins.echo_signals import confidence as conf_mod

    # Apply two positive signals — confidence should rise from 0.5 to 0.6 to 0.7.
    r1 = conf_mod.update_confidence("verify-skill", "explicit_positive")
    expect(
        "explicit_positive raises confidence to 0.6",
        r1.applied and abs(r1.new_confidence - 0.6) < 1e-9,
        detail=f"old={r1.old_confidence:.3f} → new={r1.new_confidence:.3f}",
    )
    r2 = conf_mod.update_confidence("verify-skill", "explicit_positive")
    expect(
        "explicit_positive raises confidence to 0.7",
        r2.applied and abs(r2.new_confidence - 0.7) < 1e-9,
    )

    # Apply a strong drift signal that should push us below c_min.
    r3 = conf_mod.update_confidence(
        "verify-skill", "drift_detected", severity=5.0
    )
    expect(
        "drift_detected with severity=5.0 transitions to pending_review",
        r3.applied
        and r3.new_status == conf_mod.STATUS_PENDING_REVIEW,
        detail=f"new c={r3.new_confidence:.3f}, status={r3.new_status}",
    )

    # Silence is sacred.
    r4 = conf_mod.update_confidence("verify-skill", "silence")
    expect(
        "silence does not move confidence (SACRED)",
        r4.applied and r4.new_confidence == r3.new_confidence,
    )

    # ─────────────────────────────────────────────────────────────────────
    section("6. Verify last-skill-wins across two skills in one session")
    # ─────────────────────────────────────────────────────────────────────

    # Reset module state so we start fresh from this section.
    echo_db.reset_for_tests()
    sc.clear_session_context()

    for cb in ctx.hooks["on_session_start"]:
        cb(session_id="multi-skill", platform="cli")
    _su.bump_use("first-skill")
    for cb in ctx.hooks["pre_llm_call"]:
        cb(turn_type="user")  # first-skill
    _su.bump_use("second-skill")
    for cb in ctx.hooks["pre_llm_call"]:
        cb(turn_type="user")  # second-skill
    for cb in ctx.hooks["post_tool_call"]:
        cb(tool_name="some_tool")  # second-skill
    for cb in ctx.hooks["on_session_end"]:
        cb()

    conn2 = echo_db.get_echo_conn()
    attribution = conn2.execute(
        "SELECT skill_id, signal_type FROM echo_signal_event "
        "WHERE skill_id IN ('first-skill', 'second-skill') "
        "ORDER BY event_id"
    ).fetchall()
    attr_list = [(r["skill_id"], r["signal_type"]) for r in attribution]
    expected = [
        ("first-skill", "user_turn"),
        ("second-skill", "user_turn"),
        ("second-skill", "tool_call"),
        ("second-skill", "session_ended"),
    ]
    expect(
        "last-skill-wins attribution end-to-end",
        attr_list == expected,
        detail=f"got: {attr_list}",
    )

    # ─────────────────────────────────────────────────────────────────────
    print(f"\n\033[1m── Summary ──\033[0m")
    if total_passed == total_checks:
        print(f"  \033[32m{total_passed}/{total_checks} checks passed\033[0m\n")
        sys.exit(0)
    else:
        print(f"  \033[31m{total_passed}/{total_checks} checks passed — {total_checks - total_passed} failure(s)\033[0m\n")
        sys.exit(1)

finally:
    # Always clean up the temp HERMES_HOME, even on error.
    try:
        shutil.rmtree(TMP_HOME, ignore_errors=True)
    except Exception:
        pass
