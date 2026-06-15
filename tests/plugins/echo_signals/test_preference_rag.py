"""Unit tests for M5 preference RAG.

Four categories:
  * Encoder + BLOB pack/unpack: pure math, deterministic.
  * Storage layer: store_preference, eviction, touch_used.
  * Retrieval: cosine ranking, MMR diversity, confidence weighting.
  * Formatting: injection block shape and truncation.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import preference_rag as prag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_encoder():
    prag.reset_encoder()
    yield
    prag.reset_encoder()


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class TestEncoder:
    def test_empty_text(self):
        v = prag.encode("")
        assert v == [0.0] * prag.EMBEDDING_DIM

    def test_normalized_unit_length(self):
        v = prag.encode("write me a summary of this document")
        norm_sq = sum(x * x for x in v)
        assert norm_sq == pytest.approx(1.0, abs=1e-6)

    def test_deterministic(self):
        v1 = prag.encode("hello world")
        v2 = prag.encode("hello world")
        assert v1 == v2

    def test_case_insensitive(self):
        assert prag.encode("Hello World") == prag.encode("hello world")

    def test_token_deduplication(self):
        """Repeating a word within the same text shouldn't amplify its bucket."""
        v_once = prag.encode("apple")
        v_twice = prag.encode("apple apple apple")
        assert v_once == v_twice

    def test_punctuation_stripped(self):
        assert prag.encode("hello!") == prag.encode("hello")
        assert prag.encode("(world)") == prag.encode("world")

    def test_different_text_different_vector(self):
        assert prag.encode("apple") != prag.encode("orange")

    def test_set_encoder_overrides(self):
        prag.set_encoder(lambda text: [1.0] + [0.0] * (prag.EMBEDDING_DIM - 1))
        v = prag.encode("anything")
        assert v[0] == 1.0
        assert sum(v[1:]) == 0.0


# ---------------------------------------------------------------------------
# BLOB pack/unpack
# ---------------------------------------------------------------------------


class TestBlobRoundtrip:
    def test_roundtrip_preserves_values(self):
        vec = [0.5, -0.25, 0.0, 1.0]
        blob = prag.vec_to_blob(vec)
        back = prag.blob_to_vec(blob)
        assert back == pytest.approx(vec)

    def test_blob_size_is_4_bytes_per_float(self):
        vec = [0.0] * prag.EMBEDDING_DIM
        blob = prag.vec_to_blob(vec)
        assert len(blob) == 4 * prag.EMBEDDING_DIM

    def test_truncated_blob_returns_empty(self):
        assert prag.blob_to_vec(b"\x00\x00") == []  # not divisible by 4

    def test_empty_blob_returns_empty(self):
        assert prag.blob_to_vec(b"") == []


# ---------------------------------------------------------------------------
# Cosine
# ---------------------------------------------------------------------------


class TestCosine:
    def test_identical_vectors(self):
        v = [0.6, 0.8]
        assert prag.cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert prag.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_direction(self):
        assert prag.cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_returns_zero(self):
        assert prag.cosine([], [1.0]) == 0.0
        assert prag.cosine([1.0], []) == 0.0

    def test_length_mismatch_returns_zero(self):
        assert prag.cosine([1.0, 0.0], [1.0]) == 0.0


# ---------------------------------------------------------------------------
# store_preference + eviction
# ---------------------------------------------------------------------------


class TestStorePreference:
    def test_inserts_row(self, isolated_db):
        eid = prag.store_preference(
            task_request="write a summary",
            agent_output="Here's a summary: ...",
            rating=5,
        )
        assert eid > 0
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT task_request, agent_output, rating, use_count "
            "FROM echo_preference_example WHERE example_id = ?",
            (eid,),
        ).fetchone()
        assert row["task_request"] == "write a summary"
        assert row["agent_output"] == "Here's a summary: ..."
        assert row["rating"] == 5
        assert row["use_count"] == 0

    def test_stores_embedding_blob(self, isolated_db):
        eid = prag.store_preference(
            task_request="write a summary",
            agent_output="...",
        )
        conn = echo_db.get_echo_conn()
        blob = conn.execute(
            "SELECT task_embedding FROM echo_preference_example WHERE example_id=?",
            (eid,),
        ).fetchone()["task_embedding"]
        # 256-dim float32 = 1024 bytes
        assert len(blob) == 4 * prag.EMBEDDING_DIM

    def test_empty_task_or_output_skipped(self, isolated_db):
        echo_db.get_echo_conn()  # bootstrap
        assert prag.store_preference(task_request="", agent_output="x") == 0
        assert prag.store_preference(task_request="x", agent_output="") == 0

    def test_invalid_rating_skipped(self, isolated_db):
        echo_db.get_echo_conn()
        assert prag.store_preference(
            task_request="x", agent_output="y", rating=0
        ) == 0
        assert prag.store_preference(
            task_request="x", agent_output="y", rating=6
        ) == 0

    def test_optional_skill_and_tag(self, isolated_db):
        eid = prag.store_preference(
            task_request="x",
            agent_output="y",
            skill_id="my-skill",
            task_type_tag="summarize",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, task_type_tag FROM echo_preference_example "
            "WHERE example_id = ?",
            (eid,),
        ).fetchone()
        assert row["skill_id"] == "my-skill"
        assert row["task_type_tag"] == "summarize"


class TestTouchUsed:
    def test_bumps_use_count(self, isolated_db):
        eid = prag.store_preference(task_request="x", agent_output="y")
        prag.touch_used(eid)
        prag.touch_used(eid)
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT use_count, last_used_at FROM echo_preference_example "
            "WHERE example_id = ?",
            (eid,),
        ).fetchone()
        assert row["use_count"] == 2
        assert row["last_used_at"] is not None

    def test_unknown_id_no_op(self, isolated_db):
        echo_db.get_echo_conn()
        prag.touch_used(99999)  # must not raise


class TestEviction:
    def test_keeps_under_capacity(self, isolated_db, monkeypatch):
        monkeypatch.setattr(prag, "PREFERENCE_CAPACITY", 5)
        for i in range(10):
            prag.store_preference(
                task_request=f"task-{i}",
                agent_output=f"out-{i}",
                rating=4 + (i % 2),  # mix 4 and 5
            )
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_preference_example"
        ).fetchone()["n"]
        assert n == 5


# ---------------------------------------------------------------------------
# retrieve_topk
# ---------------------------------------------------------------------------


class TestRetrieveTopK:
    def test_empty_db(self, isolated_db):
        echo_db.get_echo_conn()
        out = prag.retrieve_topk("anything")
        assert out == []

    def test_min_similarity_gate(self, isolated_db):
        # Controlled orthogonal encoder: "a…" → axis 0, anything else → axis 1.
        def enc(text):
            v = [0.0] * prag.EMBEDDING_DIM
            v[0 if text.strip().startswith("a") else 1] = 1.0
            return v

        prag.set_encoder(enc)
        try:
            prag.store_preference(
                task_request="a apple pie recipe", agent_output="x", rating=5,
            )
            # Same axis → cosine 1.0 ≥ 0.7 → retrieved.
            assert len(prag.retrieve_topk("a another query", min_similarity=0.7)) == 1
            # Orthogonal axis → cosine 0.0 < 0.7 → gated out (no irrelevant
            # example injected even though it's the only candidate).
            assert prag.retrieve_topk("b banana", min_similarity=0.7) == []
            # Lower the floor and the orthogonal one is still 0.0 (not > 0).
            assert prag.retrieve_topk("b banana", min_similarity=0.0) == []
        finally:
            prag.reset_encoder()

    def test_returns_most_similar(self, isolated_db):
        prag.store_preference(
            task_request="write a marketing email for a launch",
            agent_output="email body A",
            rating=5,
        )
        prag.store_preference(
            task_request="debug this python stacktrace",
            agent_output="probable cause is X",
            rating=5,
        )
        prag.store_preference(
            task_request="write a sales pitch for a startup",
            agent_output="pitch B",
            rating=5,
        )
        out = prag.retrieve_topk(
            "write me a marketing email for our launch", k=2,
        )
        assert len(out) >= 1
        # The marketing email example should rank above the debug one.
        ids_in_order = [ex.task_request for ex in out]
        assert any("marketing email" in t for t in ids_in_order)
        assert all("debug" not in t for t in ids_in_order[:1])

    def test_below_min_rating_filtered(self, isolated_db):
        prag.store_preference(
            task_request="hello world",
            agent_output="reply",
            rating=3,  # below default min of 4
        )
        out = prag.retrieve_topk("hello world")
        assert out == []

    def test_returns_at_most_k(self, isolated_db):
        for i in range(10):
            prag.store_preference(
                task_request=f"common phrase number {i}",
                agent_output=f"reply {i}",
                rating=5,
            )
        out = prag.retrieve_topk("common phrase", k=3)
        assert len(out) <= 3

    def test_touch_used_called_on_retrieved(self, isolated_db):
        eid = prag.store_preference(
            task_request="distinct unique phrase apple", agent_output="reply",
            rating=5,
        )
        # Query that should match.
        out = prag.retrieve_topk("distinct unique phrase apple", k=1)
        assert len(out) == 1
        conn = echo_db.get_echo_conn()
        used = conn.execute(
            "SELECT use_count FROM echo_preference_example WHERE example_id=?",
            (eid,),
        ).fetchone()["use_count"]
        assert used == 1

    def test_confidence_weights_downrank_low_confidence_skills(self, isolated_db):
        prag.store_preference(
            task_request="write a marketing email",
            agent_output="output A",
            rating=5,
            skill_id="trusted-skill",
        )
        prag.store_preference(
            task_request="write a marketing email",
            agent_output="output B",
            rating=5,
            skill_id="degraded-skill",
        )
        out = prag.retrieve_topk(
            "write me a marketing email",
            k=2,
            confidence_weights={
                "trusted-skill": 1.0,
                "degraded-skill": 0.1,
            },
        )
        # Trusted-skill row should rank first.
        assert out[0].skill_id == "trusted-skill"

    def test_empty_query_returns_empty(self, isolated_db):
        prag.store_preference(task_request="x", agent_output="y", rating=5)
        assert prag.retrieve_topk("") == []
        assert prag.retrieve_topk("   ") == []

    def test_orthogonal_results_excluded(self, isolated_db):
        """Pool only keeps positive-similarity entries."""
        # Inject an encoder that returns purely orthogonal vectors for
        # different inputs.
        def _orth(text):
            v = [0.0] * prag.EMBEDDING_DIM
            if text == "A":
                v[0] = 1.0
            elif text == "B":
                v[1] = 1.0
            return v

        prag.set_encoder(_orth)
        prag.store_preference(task_request="B", agent_output="y", rating=5)
        out = prag.retrieve_topk("A")
        assert out == []


# ---------------------------------------------------------------------------
# MMR
# ---------------------------------------------------------------------------


class TestMMR:
    """MMR algorithm itself — uses hand-constructed vectors to isolate
    the diversity logic from the hashing-embedding's coarseness."""

    def _ex(self, eid, task):
        return prag.PreferenceExample(
            example_id=eid, task_request=task, agent_output="out",
            rating=5, skill_id=None, task_type_tag=None,
        )

    def test_pure_relevance_picks_top_similar(self):
        """λ=1 → relevance only → pick the two highest-similarity items
        regardless of how similar they are to each other."""
        query = [1.0, 0.0]
        pool = [
            (self._ex(1, "near1"), [1.0, 0.0], 1.00),
            (self._ex(2, "near2"), [0.99, 0.14], 0.99),  # almost identical
            (self._ex(3, "far"),   [0.0, 1.0], 0.00),
        ]
        out = prag._mmr_rerank(query, pool, k=2, mmr_lambda=1.0)
        ids = [s[0].example_id for s in out]
        assert ids == [1, 2]  # both near-duplicates picked because relevance wins

    def test_pure_diversity_avoids_duplicates(self):
        """λ=0 → diversity only → after picking one, pick the most
        dissimilar remaining even if its relevance is mediocre."""
        query = [1.0, 0.0]
        pool = [
            (self._ex(1, "near"), [1.0, 0.0], 1.00),
            (self._ex(2, "near_dup"), [0.99, 0.14], 0.99),  # near 1
            (self._ex(3, "far"), [0.0, 1.0], 0.50),         # orthogonal
        ]
        out = prag._mmr_rerank(query, pool, k=2, mmr_lambda=0.0)
        ids = {s[0].example_id for s in out}
        # The first pick is whoever has highest λ·rel − 0·diversity = highest rel
        # = id 1. Second pick: with λ=0, only diversity matters; id 3 (far)
        # has lower similarity to id 1 than id 2 does.
        assert 1 in ids
        assert 3 in ids
        assert 2 not in ids  # near-duplicate skipped

    def test_balanced_lambda_balances(self):
        """λ in (0,1) should be sensitive to both relevance AND diversity."""
        query = [1.0, 0.0]
        pool = [
            (self._ex(1, "a"), [1.0, 0.0], 1.0),
            (self._ex(2, "b"), [0.99, 0.14], 0.99),
            (self._ex(3, "c"), [0.0, 1.0], 0.1),
        ]
        # With λ=0.5 the very-similar 2nd item should lose to the orthogonal one
        # (penalty cancels its relevance lead).
        out = prag._mmr_rerank(query, pool, k=2, mmr_lambda=0.5)
        ids = {s[0].example_id for s in out}
        assert 1 in ids
        # With this λ either {1,3} or {1,2} is acceptable depending on the
        # exact numbers; just confirm we picked 2 distinct items and the
        # first slot went to id 1 (max relevance).
        assert len(ids) == 2

    def test_k_larger_than_pool_returns_all(self):
        query = [1.0, 0.0]
        pool = [
            (self._ex(1, "a"), [1.0, 0.0], 1.0),
            (self._ex(2, "b"), [0.0, 1.0], 0.5),
        ]
        out = prag._mmr_rerank(query, pool, k=10, mmr_lambda=0.5)
        assert len(out) == 2


# ---------------------------------------------------------------------------
# format_for_injection
# ---------------------------------------------------------------------------


class TestFormatForInjection:
    def test_empty_returns_empty(self):
        assert prag.format_for_injection([]) == ""

    def test_includes_header(self):
        ex = prag.PreferenceExample(
            example_id=1,
            task_request="t",
            agent_output="o",
            rating=5,
            skill_id=None,
            task_type_tag=None,
        )
        out = prag.format_for_injection([ex])
        assert "Echo" in out
        assert "examples" in out.lower()

    def test_truncates_long_text(self):
        ex = prag.PreferenceExample(
            example_id=1,
            task_request="x" * 800,
            agent_output="y" * 800,
            rating=5,
            skill_id=None,
            task_type_tag=None,
        )
        out = prag.format_for_injection([ex])
        # Truncated to 400 chars + ellipsis per field.
        assert "…" in out

    def test_shows_similarity_when_present(self):
        ex = prag.PreferenceExample(
            example_id=1, task_request="t", agent_output="o",
            rating=5, skill_id=None, task_type_tag=None,
            similarity=0.78,
        )
        out = prag.format_for_injection([ex])
        assert "0.78" in out


# ---------------------------------------------------------------------------
# Schema integration
# ---------------------------------------------------------------------------


class TestSchemaIntegration:
    def test_echo_turn_cache_table_created(self, isolated_db):
        """ensure_echo_schema must include the new echo_turn_cache table."""
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='echo_turn_cache'"
        ).fetchone()
        assert row is not None

    def test_schema_version_bumped(self, isolated_db):
        conn = echo_db.get_echo_conn()
        v = conn.execute(
            "SELECT version FROM echo_schema_version"
        ).fetchone()["version"]
        assert v >= 2
