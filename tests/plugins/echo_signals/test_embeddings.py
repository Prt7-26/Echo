"""Unit tests for the embeddings provider layer.

We don't make real network calls. Three layers of coverage:

  * Configuration parsing — env-var combinations route correctly.
  * Fallback behavior — failed neural call flips the sticky switch
    and subsequent calls use hashing.
  * Provider installation — install_active_encoder() flips
    preference_rag's encoder atomically.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import embeddings as emb
from plugins.echo_signals import preference_rag as prag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip Echo embedding env vars and reset module state per test."""
    for k in (
        "ECHO_EMBEDDING_PROVIDER",
        "ECHO_EMBEDDING_MODEL",
        "ECHO_EMBEDDING_API_KEY",
        "ECHO_EMBEDDING_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    emb._reset_for_tests()
    prag.reset_encoder()
    yield
    emb._reset_for_tests()
    prag.reset_encoder()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


# ---------------------------------------------------------------------------
# _neural_config
# ---------------------------------------------------------------------------


class TestNeuralConfig:
    def test_no_env_returns_none(self):
        assert emb._neural_config() is None

    def test_provider_without_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        assert emb._neural_config() is None

    def test_provider_with_key_returns_dict(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "sk-fake")
        cfg = emb._neural_config()
        assert cfg is not None
        assert cfg["api_key"] == "sk-fake"
        assert cfg["model"] == "text-embedding-3-small"  # default
        assert cfg["base_url"] is None

    def test_falls_back_to_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-via-openai-var")
        cfg = emb._neural_config()
        assert cfg["api_key"] == "sk-via-openai-var"

    def test_echo_key_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "echo-key")
        monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")
        cfg = emb._neural_config()
        assert cfg["api_key"] == "echo-key"

    def test_custom_model(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        monkeypatch.setenv("ECHO_EMBEDDING_MODEL", "text-embedding-3-large")
        assert emb._neural_config()["model"] == "text-embedding-3-large"

    def test_base_url_honored(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        monkeypatch.setenv(
            "ECHO_EMBEDDING_BASE_URL", "https://openrouter.ai/api/v1",
        )
        assert emb._neural_config()["base_url"] == "https://openrouter.ai/api/v1"

    def test_unknown_provider_disabled(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "huggingface")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        assert emb._neural_config() is None


# ---------------------------------------------------------------------------
# get_active_encoder
# ---------------------------------------------------------------------------


class TestActiveEncoder:
    def test_no_config_returns_hashing(self):
        enc = emb.get_active_encoder()
        assert enc is emb._hashing_fallback
        assert not emb.is_neural_active()

    def test_configured_returns_neural(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        enc = emb.get_active_encoder()
        assert enc is emb._neural_encode
        assert emb.is_neural_active()

    def test_sticky_flip_affects_diagnostic(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        # Initially active.
        assert emb.is_neural_active()
        # Simulate a failure flipping the kill-switch.
        with emb._state_lock:
            emb._neural_disabled_sticky = True
        # Diagnostic reports inactive even though config is present.
        assert not emb.is_neural_active()


# ---------------------------------------------------------------------------
# Fallback behavior — mock the OpenAI client
# ---------------------------------------------------------------------------


class _FakeEmbeddingsAPI:
    def __init__(self, response_dim=8, fail=False):
        self.calls = 0
        self.fail = fail
        self.response_dim = response_dim

    def create(self, *, model, input):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated API failure")
        # Mimic OpenAI response shape: response.data[0].embedding
        vec = [float(self.calls) for _ in range(self.response_dim)]
        return type("Resp", (), {
            "data": [type("D", (), {"embedding": vec})()],
        })()


class _FakeClient:
    def __init__(self, fake_api):
        self.embeddings = fake_api


class TestNeuralEncode:
    def test_returns_vector_on_success(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        fake_api = _FakeEmbeddingsAPI(response_dim=4)
        monkeypatch.setattr(
            emb, "_get_openai_client", lambda cfg: _FakeClient(fake_api),
        )

        v = emb._neural_encode("hello world")
        assert v == [1.0, 1.0, 1.0, 1.0]
        assert fake_api.calls == 1

    def test_lru_cache_hits_dedupe(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        fake_api = _FakeEmbeddingsAPI(response_dim=4)
        monkeypatch.setattr(
            emb, "_get_openai_client", lambda cfg: _FakeClient(fake_api),
        )

        v1 = emb._neural_encode("same text")
        v2 = emb._neural_encode("same text")
        assert v1 == v2
        assert fake_api.calls == 1  # second call hit cache

    def test_failure_flips_sticky_and_falls_back(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        fake_api = _FakeEmbeddingsAPI(fail=True)
        monkeypatch.setattr(
            emb, "_get_openai_client", lambda cfg: _FakeClient(fake_api),
        )

        # First call fails internally → falls back to hashing.
        v = emb._neural_encode("hello")
        # Hashing default dim is 256.
        assert len(v) == prag.EMBEDDING_DIM
        # Sticky switch tripped.
        assert emb._neural_disabled_sticky is True

    def test_post_sticky_skips_api_entirely(self, monkeypatch):
        """Once tripped, subsequent calls don't even touch the API."""
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        with emb._state_lock:
            emb._neural_disabled_sticky = True

        calls_made = []
        monkeypatch.setattr(
            emb, "_get_openai_client",
            lambda cfg: (calls_made.append(1), _FakeClient(_FakeEmbeddingsAPI()))[1],
        )

        emb._neural_encode("hello")
        assert calls_made == []  # API never touched


# ---------------------------------------------------------------------------
# install_active_encoder / preference_rag swap
# ---------------------------------------------------------------------------


class TestInstallActiveEncoder:
    def test_no_config_installs_hashing(self):
        chosen = emb.install_active_encoder()
        assert chosen == "hashing"
        # preference_rag.encode now routes through hashing.
        v = prag.encode("apple")
        assert len(v) == prag.EMBEDDING_DIM

    def test_configured_installs_neural(self, monkeypatch):
        monkeypatch.setenv("ECHO_EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("ECHO_EMBEDDING_API_KEY", "x")
        fake_api = _FakeEmbeddingsAPI(response_dim=8)
        monkeypatch.setattr(
            emb, "_get_openai_client", lambda cfg: _FakeClient(fake_api),
        )

        chosen = emb.install_active_encoder()
        assert chosen == "neural"
        v = prag.encode("test")
        assert len(v) == 8  # the fake's dim
        assert fake_api.calls == 1

    def test_idempotent(self):
        emb.install_active_encoder()
        emb.install_active_encoder()
        # No exception; encoder still functional.
        v = prag.encode("anything")
        assert len(v) == prag.EMBEDDING_DIM


# ---------------------------------------------------------------------------
# clear_embedding_corpus
# ---------------------------------------------------------------------------


class TestClearEmbeddingCorpus:
    def test_deletes_both_tables(self, isolated_db):
        # Seed both tables.
        prag.store_preference(
            task_request="x", agent_output="y", rating=5,
        )
        conn = echo_db.get_echo_conn()
        conn.execute(
            "INSERT INTO echo_user_request_log "
            "(invocation_id, skill_id, session_id, user_message, embedding, ts) "
            "VALUES (NULL, NULL, NULL, 'msg', ?, 100.0)",
            (b"\x00" * 1024,),
        )
        conn.commit()

        report = emb.clear_embedding_corpus()
        assert report["preference_examples_deleted"] == 1
        assert report["user_request_log_deleted"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM echo_preference_example"
        ).fetchone()["n"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM echo_user_request_log"
        ).fetchone()["n"] == 0

    def test_empty_db_zero_counts(self, isolated_db):
        echo_db.get_echo_conn()
        report = emb.clear_embedding_corpus()
        assert report == {
            "preference_examples_deleted": 0,
            "user_request_log_deleted": 0,
        }


# ---------------------------------------------------------------------------
# Cosine resilience to dim mismatch (already in preference_rag, tested here
# to lock down the corpus-swap workflow)
# ---------------------------------------------------------------------------


class TestCosineDimMismatch:
    def test_different_dims_return_zero(self):
        v_256 = [0.5] * 256
        v_1536 = [0.5] * 1536
        assert prag.cosine(v_256, v_1536) == 0.0

    def test_same_dims_compute_normally(self):
        v_a = [1.0, 0.0]
        v_b = [1.0, 0.0]
        assert prag.cosine(v_a, v_b) == pytest.approx(1.0)
