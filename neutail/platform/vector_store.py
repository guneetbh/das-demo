"""Vector store — semantic retrieval over the catalogue (the "embedding"
capability in config/models.json).

Chroma with its bundled local ONNX MiniLM model when chromadb is importable,
using **cosine** distance (score = 1 - distance); otherwise a token-overlap
index over the same text. The index is fingerprinted against the catalogue
and rebuilt when the catalogue changes, not only when it is empty.
"""

import hashlib
import re
import threading
import time
from collections import Counter

from neutail import config, data
from neutail.data import catalogue
from neutail.platform import audit

COLLECTION_NAME = "catalogue_cosine"
_WORD_RE = re.compile(r"[a-z0-9]+")
FINGERPRINT_TTL_SECONDS = 60

# "Closely matches your search" cut-off per backend — the two score scales
# aren't comparable. MiniLM cosine similarity for a relevant short product
# text typically sits around 0.35-0.6.
MATCH_THRESHOLD = {"chroma": 0.45, "fallback": 0.6}

_lock = threading.RLock()
_collection = None
_chroma_checked = False
_fallback_index: dict[str, Counter] | None = None
_indexed_fingerprint: str | None = None
_fingerprint_cache: tuple[float, str] | None = None


def _chroma():
    global _collection, _chroma_checked
    if _chroma_checked:
        return _collection
    _chroma_checked = True
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(config.CHROMA_PATH))
        _collection = client.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
    except Exception:
        _collection = None
    return _collection


def backend() -> str:
    return "chroma" if _chroma() is not None else "fallback"


def _document_for(row: dict) -> str:
    parts = [row["name"], row["category"], row["colour"], row["material"],
             row["occasion_tags"].replace(",", " ").replace("-", " "), row["description"]]
    if row.get("is_new"):
        parts.append("new arrival new in")
    return " ".join(p for p in parts if p)


def _tokenize(text: str) -> Counter:
    return Counter(_WORD_RE.findall(text.lower()))


def _catalogue_rows() -> list[dict]:
    with data.system_scope():
        return catalogue.all_for_index()


def _fingerprint(rows: list[dict] | None = None) -> str:
    global _fingerprint_cache
    now = time.monotonic()
    if rows is None and _fingerprint_cache and now - _fingerprint_cache[0] < FINGERPRINT_TTL_SECONDS:
        return _fingerprint_cache[1]
    rows = rows if rows is not None else _catalogue_rows()
    digest = hashlib.md5()
    for row in rows:
        digest.update(f"{row['sku']}|{row['updated_at']}|{row['name']}|".encode())
    fp = f"{len(rows)}:{digest.hexdigest()}"
    _fingerprint_cache = (now, fp)
    return fp


def _fingerprint_file():
    return config.CHROMA_PATH / f"{COLLECTION_NAME}.fingerprint"


def build_index() -> int:
    global _fallback_index, _indexed_fingerprint
    with _lock:
        rows = _catalogue_rows()
        fp = _fingerprint(rows)
        coll = _chroma()
        if coll is not None:
            existing = coll.get(include=[])["ids"]
            stale = set(existing) - {r["sku"] for r in rows}
            if stale:
                coll.delete(ids=list(stale))
            for start in range(0, len(rows), 500):
                batch = rows[start:start + 500]
                coll.upsert(ids=[r["sku"] for r in batch], documents=[_document_for(r) for r in batch],
                            metadatas=[{"category": r["category"], "tier": r["tier"]} for r in batch])
            config.CHROMA_PATH.mkdir(parents=True, exist_ok=True)
            _fingerprint_file().write_text(fp)
        else:
            _fallback_index = {r["sku"]: _tokenize(_document_for(r)) for r in rows}
        _indexed_fingerprint = fp
        return len(rows)


def _ensure_fresh() -> None:
    global _indexed_fingerprint
    current = _fingerprint()
    if _indexed_fingerprint is None and _chroma() is not None and _fingerprint_file().exists():
        _indexed_fingerprint = _fingerprint_file().read_text().strip()
    if current != _indexed_fingerprint or (_chroma() is None and _fallback_index is None):
        build_index()


def _fallback_search(query: str, top_k: int) -> list[dict]:
    q_tokens = _tokenize(query)
    scored = []
    for sku, doc_tokens in (_fallback_index or {}).items():
        overlap = sum((q_tokens & doc_tokens).values())
        if overlap:
            scored.append((sku, overlap))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    top = scored[:top_k]
    max_score = top[0][1] if top else 1
    return [{"sku": sku, "score": round(score / max_score, 4)} for sku, score in top]


def semantic_search(query: str, top_k: int = 300, caller: str = "unknown") -> list[dict]:
    """Up to top_k {sku, score} dicts, best first, score in [0, 1]."""
    started = time.perf_counter()
    with _lock:
        _ensure_fresh()
    coll = _chroma()
    used = "fallback"
    hits: list[dict] = []
    error = None
    if coll is not None and query.strip():
        try:
            count = coll.count()
            result = coll.query(query_texts=[query], n_results=min(top_k, count)) if count else None
            if result:
                hits = [{"sku": sku, "score": round(max(0.0, 1.0 - dist), 4)}
                        for sku, dist in zip(result["ids"][0], result["distances"][0])]
            used = "chroma"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    if used == "fallback":
        global _fallback_index
        if _fallback_index is None:  # chroma present but failed this call
            _fallback_index = {r["sku"]: _tokenize(_document_for(r)) for r in _catalogue_rows()}
        hits = _fallback_search(query, top_k)
    audit.record(kind="retrieval", caller=caller, tool="vector_store:search",
                 outcome="ok" if error is None else "fallback",
                 model=config.load_models()["embedding"]["local"] if used == "chroma" else "token-overlap",
                 latency_ms=int((time.perf_counter() - started) * 1000),
                 detail={"query": query, "top_k": top_k, "hits": len(hits), "backend": used, "error": error})
    return hits


def match_threshold() -> float:
    return MATCH_THRESHOLD[backend()]
