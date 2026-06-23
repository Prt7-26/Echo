"""Interactive setup for Echo's auxiliary-LLM channels.

Asked once during ``hermes setup``, right after the main model picker.
The user chooses whether Echo's Layer B (NL sentiment classifier) and
Layer C (judge) should:

  1. Run against a *separate* provider (the proposal's "independent
     auditor" — recommended for any honest evaluation claim).
  2. Run against the main provider via Hermes' auxiliary fallback
     chain — convenient but loses model independence and spends the
     main provider's quota on every turn.
  3. Be disabled entirely — useful when no aux LLM is available.

The choice is persisted to ``echo.aux_mode`` in Hermes' config.yaml.
``plugins.echo_signals.aux_config`` reads it at runtime to gate each
call.

This module imports Hermes' prompt helpers lazily so importing
``plugins.echo_signals`` at plugin-load time stays cheap.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


CONFIG_ROOT = "echo"
KEY_MODE = "aux_mode"
AUX_TASKS = ("echo_classifier", "echo_judge", "echo_reason_score",
             "echo_skill_dedup", "echo_scope")


def _prompt_helpers():
    """Lazy import of Hermes' setup prompt utilities."""
    from hermes_cli import setup as _setup
    from hermes_cli.cli_output import print_info, print_warning, print_success
    return {
        "print_header": _setup.print_header,
        "print_info": print_info,
        "print_warning": print_warning,
        "print_success": print_success,
        "prompt": _setup.prompt,
        "prompt_choice": _setup.prompt_choice,
        "prompt_yes_no": _setup.prompt_yes_no,
    }


def _ensure_aux_section(config: Dict[str, Any], task: str) -> Dict[str, Any]:
    config.setdefault("auxiliary", {})
    config["auxiliary"].setdefault(task, {})
    return config["auxiliary"][task]


def _ensure_plugin_enabled(config: Dict[str, Any]) -> bool:
    """Make sure ``echo_signals`` appears in ``plugins.enabled``.

    Echo ships as a *bundled standalone* plugin, and Hermes loads bundled
    standalone plugins ONLY when they are listed in ``plugins.enabled``.
    Config migration deliberately does NOT grandfather bundled plugins
    (see hermes_cli/config.py, v20→v21), so on a fresh install nothing
    would enable Echo: the wizard could configure the aux model yet the
    plugin's ``register()`` would never run — no hooks, no signals.

    Enabling it here (during ``hermes setup``) is the canonical moment.
    Idempotent; returns True only when it actually added the entry.
    """
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}
        config["plugins"] = plugins
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
        plugins["enabled"] = enabled
    if "echo_signals" not in enabled:
        enabled.append("echo_signals")
        return True
    return False


def _ask_separate_endpoint(p, task_label: str) -> Dict[str, str]:
    """Collect base_url, api_key, model for an OpenAI-compatible endpoint."""
    p["print_info"](f"   Configure {task_label}:")
    base_url = p["prompt"](
        "Base URL (e.g. https://api.openai.com/v1)",
        default="https://api.openai.com/v1",
    )
    api_key = p["prompt"]("API key", password=True)
    model = p["prompt"](
        "Model name (e.g. gpt-4o-mini, claude-haiku-4-5-20251001)",
        default="gpt-4o-mini",
    )
    return {
        "provider": "custom",
        "base_url": base_url.strip(),
        "api_key": api_key.strip(),
        "model": model.strip(),
    }


def run_aux_provider_setup(config: Dict[str, Any]) -> None:
    """Drive the wizard step. Mutates `config` in place; the orchestrator
    persists it via save_config()."""
    p = _prompt_helpers()
    p["print_header"]("Echo Auxiliary Model (Layer B + Layer C)")

    # Enable the plugin itself BEFORE asking about the aux model. This runs
    # for every mode (separate / shared / off): even with Layer B/C off, the
    # explicit-feedback, drift, and M5 features need the plugin loaded.
    if _ensure_plugin_enabled(config):
        p["print_success"](
            "Enabled the echo_signals plugin (added to plugins.enabled)."
        )

    p["print_info"](
        "Echo's background sentiment classifier (Layer B) and judge (Layer C) "
        "are designed to call an LLM that is INDEPENDENT of the main agent. "
        "This is what makes the 'breaks same-source self-evaluation bias' claim "
        "in the proposal honest."
    )
    p["print_info"]("")
    p["print_info"]("Three options:")
    p["print_info"](
        "  1. Separate provider (RECOMMENDED): give Echo its own model + API key. "
        "Aligns with the proposal; uses a separate quota."
    )
    p["print_info"](
        "  2. Shared with main provider: Echo calls the same model as your chat. "
        "Convenient — no extra setup, no extra key — but Layer C is no longer an "
        "'independent' auditor in any meaningful sense, AND every user turn spends "
        "main-provider credit on a small classifier call."
    )
    p["print_info"](
        "  3. Disabled: Echo runs without Layer B and Layer C. The explicit "
        "channels (thumbs, A/B), drift detection, and M5 preference RAG keep "
        "working — but no automatic sentiment and no judge audit."
    )
    p["print_info"]("")

    choice = p["prompt_choice"](
        "Which mode?",
        choices=[
            "Separate provider (recommended)",
            "Shared with main provider",
            "Disabled (no Layer B / Layer C)",
        ],
        default=0,
    )

    config.setdefault(CONFIG_ROOT, {})

    if choice == 1:
        config[CONFIG_ROOT][KEY_MODE] = "shared"
        p["print_warning"](
            "Layer C will share its underlying model with the agent — "
            "the proposal's independence claim is weakened. "
            "Every user turn will also spend a small Layer B call against your main "
            "provider quota."
        )
        return

    if choice == 2:
        config[CONFIG_ROOT][KEY_MODE] = "off"
        p["print_warning"](
            "Layer B (NL sentiment) and Layer C (judge) are now OFF. "
            "Explicit feedback (thumbs / scope), drift, and M5 still work."
        )
        return

    # choice == 0: separate provider.
    config[CONFIG_ROOT][KEY_MODE] = "separate"

    same_for_both = p["prompt_yes_no"](
        "Use the same endpoint for all of Echo's auxiliary tasks "
        "(Layer B classifier, Layer C judge, reason scorer)?",
        default=True,
    )

    if same_for_both:
        endpoint = _ask_separate_endpoint(p, "Echo auxiliary tasks (one endpoint)")
        for task in AUX_TASKS:
            section = _ensure_aux_section(config, task)
            section.update(endpoint)
        p["print_success"](
            f"Configured {', '.join(AUX_TASKS)} → {endpoint['base_url']} "
            f"({endpoint['model']})"
        )
    else:
        clf = _ask_separate_endpoint(p, "Layer B classifier (echo_classifier)")
        jdg = _ask_separate_endpoint(p, "Layer C judge (echo_judge)")
        rsn = _ask_separate_endpoint(p, "Reason scorer (echo_reason_score)")
        ddp = _ask_separate_endpoint(p, "Skill dedup check (echo_skill_dedup)")
        scp = _ask_separate_endpoint(p, "Scope option generator (echo_scope)")
        _ensure_aux_section(config, "echo_classifier").update(clf)
        _ensure_aux_section(config, "echo_judge").update(jdg)
        _ensure_aux_section(config, "echo_reason_score").update(rsn)
        _ensure_aux_section(config, "echo_skill_dedup").update(ddp)
        _ensure_aux_section(config, "echo_scope").update(scp)
        p["print_success"](
            "Configured all five Echo aux tasks (Layer B classifier, Layer C "
            "judge, reason scorer, skill dedup, scope generator) with separate "
            "endpoints."
        )
