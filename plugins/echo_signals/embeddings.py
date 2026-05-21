"""Pluggable embedding providers for Echo.

Echo's M1 (semantic recurrence) and M5 (preference retrieval) both
rely on text → vector → cosine. The default encoder
(preference_rag._default_encode) is a stdlib hashing scheme — zero
cost, but lexical-only. This module adds a neural alternative routed
through any OpenAI-compatible embeddings endpoint.

Configuration is environment-variable driven, deliberately bypassing
Hermes' private auxiliary_client API so Echo doesn't depend on
under-the-hood Hermes internals:

  ECHO_EMBEDDING_PROVIDER  — 'openai' to enable; anything else
                             (or unset) keeps the hashing default.
  ECHO_EMBEDDING_MODEL     — defaults to 'text-embedding-3-small'.
  ECHO_EMBEDDING_API_KEY   — falls back to OPENAI_API_KEY.
  ECHO_EMBEDDING_BASE_URL  — for OpenAI-compatible self-hosted /
                             routed endpoints (e.g. via OpenRouter).

Failure handling: a single network/auth failure logs a warning and
flips Echo to "neural disabled" in the current process — subsequent
calls go through the hashing fallback. This prevents an outage from
spamming the agent loop with retries on every user turn.

Dimensional caveat: switching from hashing (256-dim) to neural
(typically 1536-dim) makes existing rows in echo_preference_example
and echo_user_request_log incompatible. cosine() in preference_rag is
already defensive (returns 0.0 on length mismatch), so stale rows
silently never match. To repopulate, call clear_embedding_corpus()
once after the switch and let signals re-populate organically.
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Sticky kill-switch: once a neural call fails we don't keep trying.
# Tests can reset via _reset_for_tests.
_neural_disabled_sticky = False
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# OpenAI-compatible neural encoder
# ---------------------------------------------------------------------------


def _neural_config() -> Optional[dict]:
    """Return a config dict if neural embeddings are enabled, else None."""
    provider = os.environ.get("ECHO_EMBEDDING_PROVIDER", "").strip().lower()
    if provider != "openai":
        return None
    api_key = (
        os.environ.get("ECHO_EMBEDDING_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        return None
    return {
        "model": os.environ.get(
            "ECHO_EMBEDDING_MODEL", "text-embedding-3-small",
        ),
        "api_key": api_key,
        "base_url": os.environ.get("ECHO_EMBEDDING_BASE_URL") or None,
    }


# Module-level OpenAI client, lazy-constructed and cached so we don't pay
# the TCP/TLS setup cost on every encode.
_openai_client = None


def _get_openai_client(cfg: dict):
    global _openai_client
    if _openai_client is None:
        # openai is a core Hermes dependency (see pyproject.toml), so it
        # is always importable here.
        from openai import OpenAI

        kwargs = {"api_key": cfg["api_key"]}
        if cfg.get("base_url"):
            kwargs["base_url"] = cfg["base_url"]
        _openai_client = OpenAI(**kwargs)
    return _openai_client


def _call_neural_api(text: str, cfg: dict) -> list[float]:
    """Single API call. Raises on any failure (caller decides what to do)."""
    client = _get_openai_client(cfg)
    response = client.embeddings.create(model=cfg["model"], input=text)
    # OpenAI returns response.data[0].embedding as a list[float].
    return list(response.data[0].embedding)


@lru_cache(maxsize=2048)
def _cached_neural(text: str) -> tuple[float, ...]:
    """LRU-cached version. Tuples are hashable; the wrapper converts to
    list for callers that mutate the result."""
    cfg = _neural_config()
    if cfg is None:
        raise RuntimeError("neural embedding not configured")
    return tuple(_call_neural_api(text, cfg))


def _neural_encode(text: str) -> list[float]:
    """Public neural encoder.

    Honors the sticky kill-switch: once we've taken a failure in this
    process, subsequent calls fall through to the hashing fallback
    automatically. Caller doesn't need to know which path served it.
    """
    global _neural_disabled_sticky
    with _state_lock:
        if _neural_disabled_sticky:
            return _hashing_fallback(text)

    try:
        return list(_cached_neural(text))
    except Exception as exc:
        with _state_lock:
            if not _neural_disabled_sticky:
                logger.warning(
                    "Echo neural embedding failed (%s); falling back to "
                    "hashing for the rest of this process. Set "
                    "ECHO_EMBEDDING_PROVIDER='' to silence this warning.",
                    exc,
                )
                _neural_disabled_sticky = True
        return _hashing_fallback(text)


def _hashing_fallback(text: str) -> list[float]:
    """Defer to preference_rag's default hashing encoder."""
    # Late import to avoid a circular pull (preference_rag may import us
    # at init).
    from . import preference_rag as _prag

    return _prag._default_encode(text)


# ---------------------------------------------------------------------------
# Provider selection + Echo plugin initialization
# ---------------------------------------------------------------------------


def get_active_encoder() -> Callable[[str], list[float]]:
    """Return the encoder Echo should use right now.

    Order of preference:
      1. ECHO_EMBEDDING_PROVIDER=openai + key present → neural
      2. Anything else → hashing
    """
    if _neural_config() is not None:
        return _neural_encode
    return _hashing_fallback


def is_neural_active() -> bool:
    """Report whether the active encoder is neural.

    Useful for diagnostics surfaces (dashboard status panel, logs).
    Reflects current configuration AND the sticky kill-switch — if a
    failure has tripped it, this returns False even when config is
    present.
    """
    if _neural_config() is None:
        return False
    with _state_lock:
        return not _neural_disabled_sticky


def install_active_encoder() -> str:
    """Install the configured encoder onto preference_rag.set_encoder().

    Called once at plugin register time. Returns the name of the path
    chosen — 'neural', 'hashing', or 'neural-sticky-fallback' — for
    logging. Safe to call multiple times (idempotent).
    """
    from . import preference_rag as _prag

    enc = get_active_encoder()
    _prag.set_encoder(enc)
    if enc is _neural_encode:
        return "neural"
    return "hashing"


def clear_embedding_corpus() -> dict:
    """Wipe all stored embeddings (preferences + user-request log).

    Use this after switching ECHO_EMBEDDING_PROVIDER (between hashing
    and neural) so the corpus is repopulated with vectors that match
    the new encoder's dimensionality.

    Returns row-delete counts as a small report dict.
    """
    from .db import get_echo_conn

    conn = get_echo_conn()
    cur1 = conn.execute("DELETE FROM echo_preference_example")
    cur2 = conn.execute("DELETE FROM echo_user_request_log")
    conn.commit()
    return {
        "preference_examples_deleted": cur1.rowcount,
        "user_request_log_deleted": cur2.rowcount,
    }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:
    """Drop module-level state so each test starts clean.

    The lru_cache is per-process, so we explicitly clear it. The kill-
    switch and the cached OpenAI client also reset.
    """
    global _neural_disabled_sticky, _openai_client
    with _state_lock:
        _neural_disabled_sticky = False
        _openai_client = None
    _cached_neural.cache_clear()
