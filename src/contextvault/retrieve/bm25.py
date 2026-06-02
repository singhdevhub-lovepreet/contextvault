"""BM25 inverted index with workspace-scope filtering.

Pure stdlib. Okapi BM25 with k1=1.5, b=0.75 (the values that have been
empirically the best general-purpose defaults for short-document retrieval
across both academic and industry benchmarks).

Two design points that diverge from the upstream:

  1. *Class-based*, not a CLI script. The index is constructed in memory,
     populated with ``add_document``, and persisted via ``save`` / ``load``.
     Concurrency is the caller's responsibility (wrap mutations in
     ``vault.lock``); the index itself does no I/O during mutation.

  2. *Workspace scope is a first-class field*, not bolted on. Each document
     carries a ``workspace`` metadata key. ``query`` accepts ``scope`` —
     either ``None`` (global) or a workspace id — and filters posting-list
     traversal to documents matching the scope plus globally-shared ones
     (workspace=None).

Adapted from claude-obsidian/scripts/bm25-index.py for the index/query
math; restructured for in-process use and per-workspace filtering.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

__all__ = [
    "BM25Index",
    "QueryHit",
    "tokenize",
]


K1 = 1.5
B = 0.75
SCHEMA_VERSION = 1

# Small, conservative English stopword list — keeps recall high. ASCII-only
# by intent; tokens in other scripts pass through untouched.
_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "he", "her", "him", "his", "i", "if", "in", "is", "it", "its", "of", "on", "or", "that", "the", "their", "them", "they", "this", "to", "was", "were", "will", "with", "you", "your"]
)

# Unicode-aware: matches letters/digits in any script. Preserves internal
# apostrophes and hyphens ("user's", "well-formed") as single tokens.
# Symbol-only and emoji-only strings fail the leading ``\w`` anchor and
# are correctly skipped.
_TOKEN_RE = re.compile(r"\w[\w'\-]*", re.UNICODE)


class QueryHit(TypedDict):
    doc_id: str
    score: float
    workspace: str | None


class _DocEntry(TypedDict):
    dl: int
    workspace: str | None


def tokenize(text: str) -> list[str]:
    """Lowercase, drop stopwords, drop single-char tokens.

    Single-char tokens are dropped because they carry no useful signal and
    blow up the postings size — every ``a`` / ``i`` / ``x`` would otherwise
    appear in nearly every document.
    """
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOPWORDS and len(t) > 1
    ]


class BM25Index:
    """Append-only BM25 inverted index.

    Mutation methods (``add_document``, ``remove_document``) update internal
    state in O(|tokens|). ``query`` is O(|qterms| · avg_postings_per_term)
    with the scope-filter pruning the postings traversal in-loop.
    """

    def __init__(self, *, k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, _DocEntry] = {}
        self._df: Counter[str] = Counter()
        # postings: term → list[(doc_id, term_freq_in_doc)]
        self._postings: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self._total_dl: int = 0

    # ---- mutation ------------------------------------------------------

    def add_document(self, doc_id: str, text: str, *, workspace: str | None = None) -> None:
        """Add or replace a document. Re-adding the same ``doc_id`` overwrites."""
        if doc_id in self._docs:
            self.remove_document(doc_id)
        tokens = tokenize(text)
        tf = Counter(tokens)
        self._docs[doc_id] = {"dl": len(tokens), "workspace": workspace}
        self._total_dl += len(tokens)
        for term, count in tf.items():
            self._df[term] += 1
            self._postings[term].append((doc_id, count))

    def remove_document(self, doc_id: str) -> bool:
        """Remove ``doc_id`` if present. Returns True if a doc was removed."""
        entry = self._docs.pop(doc_id, None)
        if entry is None:
            return False
        self._total_dl -= entry["dl"]
        # Strip from postings + df. Only walk terms that could plausibly
        # contain the doc — we don't track a reverse index, so walk all,
        # but the cost is bounded by removal frequency (rare).
        empty_terms: list[str] = []
        for term, plist in self._postings.items():
            new_plist = [(d, c) for (d, c) in plist if d != doc_id]
            if len(new_plist) != len(plist):
                self._df[term] -= len(plist) - len(new_plist)
                if not new_plist:
                    empty_terms.append(term)
                else:
                    self._postings[term] = new_plist
        for term in empty_terms:
            del self._postings[term]
            del self._df[term]
        return True

    # ---- query ---------------------------------------------------------

    @property
    def doc_count(self) -> int:
        return len(self._docs)

    @property
    def avg_dl(self) -> float:
        if not self._docs:
            return 0.0
        return self._total_dl / len(self._docs)

    def query(
        self,
        text: str,
        *,
        top_k: int = 20,
        scope: str | None = None,
    ) -> list[QueryHit]:
        """Score documents against ``text`` and return the top ``top_k``.

        ``scope=None`` searches all documents (global). ``scope=<workspace>``
        searches that workspace plus globally-shared docs (those added with
        ``workspace=None``).
        """
        qterms = tokenize(text)
        if not qterms or not self._docs:
            return []

        n = len(self._docs)
        avg_dl_safe = self.avg_dl or 1.0
        scores: dict[str, float] = defaultdict(float)

        for term in set(qterms):
            plist = self._postings.get(term)
            if not plist:
                continue
            df = self._df[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for doc_id, tf in plist:
                entry = self._docs[doc_id]
                if not _matches_scope(entry["workspace"], scope):
                    continue
                dl = entry["dl"]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / avg_dl_safe)
                scores[doc_id] += idf * (tf * (self.k1 + 1)) / denom

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            QueryHit(
                doc_id=doc_id,
                score=round(score, 6),
                workspace=self._docs[doc_id]["workspace"],
            )
            for doc_id, score in ranked
        ]

    # ---- persistence ---------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot of the index."""
        return {
            "schema_version": SCHEMA_VERSION,
            "params": {"k1": self.k1, "b": self.b},
            "doc_count": len(self._docs),
            "avg_dl": self.avg_dl,
            "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "docs": dict(self._docs),
            "vocab": {
                term: {
                    "df": self._df[term],
                    "postings": [list(p) for p in self._postings[term]],
                }
                for term in sorted(self._df)
            },
        }

    def save(self, path: str | os.PathLike[str]) -> None:
        """Atomically write the index to ``path`` (JSON)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except Exception:
            from contextlib import suppress

            with suppress(OSError):
                os.unlink(tmp)
            raise

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> BM25Index:
        """Load an index previously written by :meth:`save`."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported BM25 index schema: {data.get('schema_version')!r}"
            )
        params = data["params"]
        idx = cls(k1=params["k1"], b=params["b"])
        for doc_id, entry in data["docs"].items():
            idx._docs[doc_id] = {
                "dl": entry["dl"],
                "workspace": entry.get("workspace"),
            }
            idx._total_dl += entry["dl"]
        for term, term_data in data["vocab"].items():
            idx._df[term] = term_data["df"]
            idx._postings[term] = [
                (doc_id, count) for doc_id, count in term_data["postings"]
            ]
        return idx

    # ---- bulk loading -------------------------------------------------

    @classmethod
    def from_documents(
        cls,
        docs: Iterable[tuple[str, str, str | None]],
        *,
        k1: float = K1,
        b: float = B,
    ) -> BM25Index:
        """Construct an index from ``(doc_id, text, workspace)`` triples."""
        idx = cls(k1=k1, b=b)
        for doc_id, text, workspace in docs:
            idx.add_document(doc_id, text, workspace=workspace)
        return idx


def _matches_scope(doc_workspace: str | None, scope: str | None) -> bool:
    """Return True iff ``doc_workspace`` is visible under ``scope``.

    Rules:
      * scope=None (global query)        → all docs visible
      * scope=<ws>, doc_workspace=None   → shared (visible to all workspaces)
      * scope=<ws>, doc_workspace=<ws>   → same workspace, visible
      * scope=<ws>, doc_workspace=<oth>  → other workspace, hidden
    """
    if scope is None:
        return True
    if doc_workspace is None:
        return True
    return doc_workspace == scope
