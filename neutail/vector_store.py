"""Vector Store — semantic candidate retrieval for MUSE.

Replaces what `_candidates()` used to do: match a query against exactly
three hardcoded phrases ("date"+"night", "work", "gym") via
`occasion_tags LIKE`, or, for literally everything else (including a
plain "jeans" or "gift ideas"), hand back the *entire* catalogue and let
the ranker sort it out. That meant most free-text queries got zero
query-relevance filtering at retrieval time.

Same live/fallback shape as gateway.py (model calls) and session_store.py
(session memory): Chroma — a real embedded vector database, persisted to
data/chroma/ — when importable, using its bundled local ONNX MiniLM
embedding model (downloaded once, cached under ~/.cache/chroma/, no API
key or network needed after that). If chromadb isn't installed, or
initializing it fails for any reason, falls back to a pure-Python
token-overlap index built from the same catalogue text — still real
retrieval, just a cruder relevance signal, so a missing optional
dependency degrades quality rather than breaking search entirely.
"""

import re
from collections import Counter
from pathlib import Path

from neutail.db import get_connection

CHROMA_PATH = str(Path(__file__).resolve().parent.parent / "data" / "chroma")
COLLECTION_NAME = "catalogue"
_WORD_RE = re.compile(r"[a-z0-9]+")

_client = None
_collection = None
_checked = False
_fallback_index: dict[str, Counter] | None = None  # sku -> token counts, built lazily


def _chroma_collection():
    global _client, _collection, _checked
    if _checked:
        return _collection
    _checked = True
    try:
        import chromadb

        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        _collection = _client.get_or_create_collection(COLLECTION_NAME)
    except Exception:
        _collection = None
    return _collection


def backend() -> str:
    """Which store is actually serving searches right now — surfaced in the
    Admin tab's vector-search panel, same idea as session_store.backend()."""
    return "chroma" if _chroma_collection() is not None else "fallback"


def _document_for(row) -> str:
    return f"{row['name']} {row['category']} {row['occasion_tags'].replace(',', ' ')}"


def _tokenize(text: str) -> Counter:
    return Counter(_WORD_RE.findall(text.lower()))


def build_index() -> int:
    """(Re)builds the index from the current `catalogue` table. Called once
    at seed time (neutail.seed.seed()) and lazily on first search if the
    collection is empty — so an existing data/neutail.db that predates
    this feature still works without a manual re-seed."""
    conn = get_connection()
    rows = conn.execute("SELECT sku, name, category, occasion_tags FROM catalogue").fetchall()
    conn.close()

    coll = _chroma_collection()
    if coll is not None:
        coll.upsert(
            ids=[row["sku"] for row in rows],
            documents=[_document_for(row) for row in rows],
            metadatas=[{"category": row["category"]} for row in rows],
        )
    else:
        global _fallback_index
        _fallback_index = {row["sku"]: _tokenize(_document_for(row)) for row in rows}
    return len(rows)


def _fallback_search(query: str, top_k: int) -> list[dict]:
    if _fallback_index is None:
        build_index()
    q_tokens = _tokenize(query)
    scored = []
    for sku, doc_tokens in (_fallback_index or {}).items():
        overlap = sum((q_tokens & doc_tokens).values())
        if overlap:
            scored.append((sku, overlap))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[:top_k]
    max_score = top[0][1] if top else 1
    return [{"sku": sku, "score": round(score / max_score, 4)} for sku, score in top]


def semantic_search(query: str, top_k: int = 300) -> list[dict]:
    """Returns up to top_k {sku, score} dicts, best first. `score` is in
    [0, 1] on both backends but isn't comparable across them (chroma's is
    derived from embedding distance, the fallback's from token overlap) —
    only meaningful for ranking within a single call."""
    coll = _chroma_collection()
    if coll is not None:
        try:
            count = coll.count()
            if count == 0:
                build_index()
                count = coll.count()
            if count == 0:
                _log(query, top_k, live=True, count=0)
                return []
            result = coll.query(query_texts=[query], n_results=min(top_k, count))
            hits = [
                {"sku": sku, "score": round(1 / (1 + dist), 4)}
                for sku, dist in zip(result["ids"][0], result["distances"][0])
            ]
            _log(query, top_k, live=True, count=len(hits))
            return hits
        except Exception as exc:
            _log(query, top_k, live=False, count=0,
                 detail=f"chroma query failed, falling back: {type(exc).__name__}: {exc}")

    hits = _fallback_search(query, top_k)
    _log(query, top_k, live=False, count=len(hits))
    return hits


def _log(query: str, top_k: int, live: bool, count: int, detail: str | None = None) -> None:
    backend_name = "chroma" if live else "fallback"
    message = detail or f"live={live} ({backend_name}) query={query!r} top_k={top_k} hits={count}"
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO audit_log (caller, tool, allowed, detail) VALUES (?, ?, 1, ?)",
            ("muse_agent", "vector_store:search", message),
        )
    conn.close()


if __name__ == "__main__":
    # python -m neutail.vector_store [N]  — peek at the first N indexed entries
    import sys

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(f"backend: {backend()}")
    coll = _chroma_collection()
    if coll is not None:
        print(f"entries: {coll.count()}")
        sample = coll.get(limit=limit, include=["documents", "metadatas"])
        for sku, doc, meta in zip(sample["ids"], sample["documents"], sample["metadatas"]):
            print(f"{sku:<14} {meta['category']:<12} {doc}")
    else:
        if _fallback_index is None:
            build_index()
        print(f"entries: {len(_fallback_index or {})}")
        for sku, tokens in list((_fallback_index or {}).items())[:limit]:
            print(f"{sku:<14} {dict(tokens)}")
