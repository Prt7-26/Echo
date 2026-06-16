"""Runtime gate for Echo's auxiliary-LLM channels (Layer B + Layer C).

The two channels live behind separate config keys so a user can:

  1. Configure a *separate* provider for echo_classifier / echo_judge
     in ``auxiliary.echo_classifier`` / ``auxiliary.echo_judge``. This is
     what the proposal's "independent auditor" claim requires.

  2. Reuse their main chat provider (the historical default — keeps
     working without any extra config, but the judge then shares an
     underlying model with the agent it grades).

  3. Disable Layer B/C entirely. Useful when no aux LLM is reachable
     (no second API key, or main provider is exhausted) so each turn
     does not silently fire a daemon thread that crashes on 402.

The mode is read from ``echo.aux_mode`` in Hermes' config.yaml. Three
values:

    "separate"  — only fire when the per-task auxiliary.echo_* config
                  has at least a provider OR a base_url+api_key. If not
                  configured, the channel is OFF (does not fall back to
                  main).

    "shared"    — historical behaviour: fire and let
                  ``_resolve_task_provider_model`` walk its fallback
                  chain (per-task → aux default → main). Layer B/C end
                  up calling the same model as the agent.

    "off"       — never fire, regardless of config.

Default when no key is present (back-compat): "shared".

The check is per-call (not memoised) so the setup wizard can flip the
mode mid-process and the next call picks it up.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

logger = logging.getLogger(__name__)


AuxMode = Literal["separate", "shared", "off"]
DEFAULT_MODE: AuxMode = "shared"

CONFIG_KEY_ROOT = "echo"
CONFIG_KEY_MODE = "aux_mode"

# Task names Echo registers via Hermes' auxiliary client.
TASK_CLASSIFIER = "echo_classifier"
TASK_JUDGE = "echo_judge"
TASK_REASON_SCORE = "echo_reason_score"
TASK_SKILL_DEDUP = "echo_skill_dedup"
TASK_SCOPE = "echo_scope"


def _load_config() -> dict:
    """Best-effort load of Hermes config. Returns {} on any failure so we
    never crash a hot path because the wizard isn't installed yet."""
    try:
        from hermes_cli.config import load_config  # type: ignore
        return load_config() or {}
    except Exception as exc:
        logger.debug("aux_config: load_config failed: %s", exc, exc_info=True)
        return {}


def get_aux_mode() -> AuxMode:
    """Read echo.aux_mode from config. Defaults to 'shared' for back-compat."""
    cfg = _load_config()
    section = cfg.get(CONFIG_KEY_ROOT) if isinstance(cfg, dict) else None
    if isinstance(section, dict):
        value = str(section.get(CONFIG_KEY_MODE, DEFAULT_MODE)).strip().lower()
        if value in ("separate", "shared", "off"):
            return value  # type: ignore[return-value]
    return DEFAULT_MODE


def _task_has_separate_config(task: str, cfg: Optional[dict] = None) -> bool:
    """True if the user gave the task its own provider in config.yaml.

    We require either:
      - an explicit provider (e.g. ``provider: openai``), OR
      - a base_url + api_key pair (the "custom endpoint" shape).
    """
    if cfg is None:
        cfg = _load_config()
    if not isinstance(cfg, dict):
        return False
    aux = cfg.get("auxiliary")
    if not isinstance(aux, dict):
        return False
    task_cfg = aux.get(task)
    if not isinstance(task_cfg, dict):
        return False

    provider = str(task_cfg.get("provider", "")).strip()
    base_url = str(task_cfg.get("base_url", "")).strip()
    api_key = str(task_cfg.get("api_key", "")).strip()
    if provider:
        return True
    if base_url and api_key:
        return True
    return False


def aux_enabled_for(task: str) -> bool:
    """Should Echo fire this auxiliary task right now?

    Combines the user-chosen mode with what's actually configured.
    """
    mode = get_aux_mode()
    if mode == "off":
        return False
    if mode == "shared":
        return True
    # mode == "separate"
    return _task_has_separate_config(task)


# Convenience wrappers — one per Echo task — so call sites read cleanly.

def classifier_enabled() -> bool:
    return aux_enabled_for(TASK_CLASSIFIER)


def judge_enabled() -> bool:
    return aux_enabled_for(TASK_JUDGE)


def reason_scorer_enabled() -> bool:
    return aux_enabled_for(TASK_REASON_SCORE)


def skill_dedup_enabled() -> bool:
    return aux_enabled_for(TASK_SKILL_DEDUP)


def scope_enabled() -> bool:
    return aux_enabled_for(TASK_SCOPE)
