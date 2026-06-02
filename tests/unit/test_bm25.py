"""Tests for contextvault.retrieve.bm25 — index, scoring, scope filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextvault.retrieve.bm25 import BM25Index, tokenize


class TestTokenize:
    def test_lowercases(self) -> None:
        assert tokenize("Hello WORLD") == ["hello", "world"]

    def test_drops_stopwords(self) -> None:
        # "the quick brown fox" → ['quick', 'brown', 'fox']  (the is dropped)
        assert tokenize("the quick brown fox") == ["quick", "brown", "fox"]

    def test_drops_single_char(self) -> None:
        assert tokenize("a b c") == []

    def test_preserves_hyphenated(self) -> None:
        assert tokenize("well-formed") == ["well-formed"]

    def test_preserves_apostrophes(self) -> None:
        assert tokenize("user's input") == ["user's", "input"]

    def test_drops_pure_symbols(self) -> None:
        assert tokenize("!@# ... ---") == []

    def test_unicode_preserved(self) -> None:
        # CJK + accented Latin both tokenize.
        assert "café" in tokenize("the café was open")
        assert "中文" in tokenize("here is 中文 text")

    def test_punctuation_around_words(self) -> None:
        assert tokenize("(hello, world!)") == ["hello", "world"]


class TestEmptyIndex:
    def test_doc_count_zero(self) -> None:
        idx = BM25Index()
        assert idx.doc_count == 0
        assert idx.avg_dl == 0.0

    def test_query_returns_empty(self) -> None:
        idx = BM25Index()
        assert idx.query("anything") == []


class TestAddAndQuery:
    def test_single_doc_hits_its_own_term(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "quick brown fox jumps over lazy dog")
        hits = idx.query("fox")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"
        assert hits[0]["score"] > 0

    def test_missing_term_returns_empty(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "alpha beta gamma")
        assert idx.query("nonexistent") == []

    def test_multi_doc_ranking(self) -> None:
        # Doc that mentions the term more often + has the rarer term should rank higher.
        idx = BM25Index()
        idx.add_document("d1", "apple apple apple banana cherry")
        idx.add_document("d2", "apple banana orange grape pear")
        idx.add_document("d3", "kiwi orange grape pear")
        hits = idx.query("apple")
        assert [h["doc_id"] for h in hits[:2]] == ["d1", "d2"]

    def test_overwrite_via_readd(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "alpha beta gamma")
        idx.add_document("d1", "delta epsilon zeta")
        # 'alpha' should no longer hit d1
        assert idx.query("alpha") == []
        assert idx.query("delta")[0]["doc_id"] == "d1"

    def test_remove_document(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "alpha beta gamma")
        idx.add_document("d2", "alpha delta epsilon")
        assert idx.remove_document("d1") is True
        assert [h["doc_id"] for h in idx.query("alpha")] == ["d2"]
        assert idx.remove_document("d1") is False  # already gone

    def test_top_k_limits_results(self) -> None:
        idx = BM25Index()
        for i in range(5):
            idx.add_document(f"d{i}", f"alpha beta-{i}")
        assert len(idx.query("alpha", top_k=2)) == 2


class TestWorkspaceScope:
    def test_global_query_sees_all(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "auth refactor", workspace="-Users-a")
        idx.add_document("d2", "auth cleanup", workspace="-Users-b")
        idx.add_document("d3", "auth notes")  # global / shared
        ids = {h["doc_id"] for h in idx.query("auth")}
        assert ids == {"d1", "d2", "d3"}

    def test_workspace_scope_hides_others(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "auth refactor", workspace="-Users-a")
        idx.add_document("d2", "auth cleanup", workspace="-Users-b")
        ids = {h["doc_id"] for h in idx.query("auth", scope="-Users-a")}
        assert ids == {"d1"}

    def test_workspace_scope_includes_shared(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "auth refactor", workspace="-Users-a")
        idx.add_document("d3", "auth glossary")  # shared
        ids = {h["doc_id"] for h in idx.query("auth", scope="-Users-a")}
        assert ids == {"d1", "d3"}


class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.add_document("d1", "alpha beta gamma", workspace="-w-a")
        idx.add_document("d2", "alpha delta", workspace="-w-b")
        idx.add_document("d3", "beta epsilon")
        path = tmp_path / "bm25" / "index.json"
        idx.save(path)
        assert path.is_file()

        loaded = BM25Index.load(path)
        assert loaded.doc_count == 3
        # Query against loaded index returns same ranking as original
        orig = idx.query("alpha beta")
        new = loaded.query("alpha beta")
        assert [(h["doc_id"], h["score"]) for h in orig] == [
            (h["doc_id"], h["score"]) for h in new
        ]
        # Workspaces survive the round trip
        ws_a_hits = {h["doc_id"] for h in loaded.query("alpha beta", scope="-w-a")}
        assert ws_a_hits == {"d1", "d3"}

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.add_document("d1", "alpha")
        idx.save(tmp_path / "a" / "b" / "c" / "index.json")
        assert (tmp_path / "a" / "b" / "c" / "index.json").is_file()

    def test_load_rejects_unknown_schema(self, tmp_path: Path) -> None:
        bogus = tmp_path / "idx.json"
        bogus.write_text('{"schema_version": 99, "params": {"k1": 1.5, "b": 0.75},'
                         ' "doc_count": 0, "avg_dl": 0, "docs": {}, "vocab": {}}')
        with pytest.raises(ValueError, match="schema"):
            BM25Index.load(bogus)


class TestBulkLoad:
    def test_from_documents(self) -> None:
        idx = BM25Index.from_documents(
            [
                ("d1", "alpha beta", "-w-a"),
                ("d2", "alpha gamma", "-w-b"),
                ("d3", "delta epsilon", None),
            ]
        )
        assert idx.doc_count == 3
        ids = {h["doc_id"] for h in idx.query("alpha")}
        assert ids == {"d1", "d2"}


class TestScoringInvariants:
    def test_more_matches_ranks_higher(self) -> None:
        idx = BM25Index()
        idx.add_document("d1", "alpha alpha alpha alpha alpha")
        idx.add_document("d2", "alpha beta gamma delta epsilon")
        hits = idx.query("alpha")
        assert hits[0]["doc_id"] == "d1"
        assert hits[0]["score"] > hits[1]["score"]

    def test_rarer_term_has_higher_idf_signal(self) -> None:
        # If 'rare' appears in only one doc and 'common' in all five,
        # the doc with 'rare' should win even with equal term frequency.
        idx = BM25Index()
        idx.add_document("d1", "common rare")
        for i in range(2, 6):
            idx.add_document(f"d{i}", "common common common")
        hits = idx.query("rare common")
        assert hits[0]["doc_id"] == "d1"
