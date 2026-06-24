"""
Embedding + vector store layer.

Two collections live side-by-side:

  * `orders`     - structured Order records (one doc per order). The natural
                   language description is what gets embedded; Order fields
                   are filterable metadata.
  * `documents`  - arbitrary text chunks (PDF pages, etc). Used for free-text
                   ingestion. Metadata is a flexible flat dict with `kind`
                   and `source` always present.

Search merges both collections, sorted by similarity, so the chat endpoint
can pull from orders AND documents in one call.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable, Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from .models import Order


# --- embedder ---

class Embedder:
    """Thin wrapper around sentence-transformers."""

    def __init__(self, model_name: str):
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # normalize_embeddings=True gives us cosine-sim via dot product
        vecs = self.model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vecs.tolist()


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder(os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))


# --- collections ---

def _chroma_client() -> chromadb.api.ClientAPI:
    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "./data/chroma")
    return chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )


@lru_cache(maxsize=1)
def _col(name: str):
    client = _chroma_client()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def get_orders_collection():
    name = os.environ.get("COLLECTION_NAME", "order_guides")
    return _col(name)


def get_documents_collection():
    name = os.environ.get("DOCS_COLLECTION_NAME", "documents")
    return _col(name)


def _coerce_metadata(meta: dict) -> dict:
    """Chroma metadata must be flat str/int/bool/float. Drop nested values
    silently (caller decides what to keep). Booleans -> ints."""
    out: dict = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = int(v)
        elif isinstance(v, (str, int, float)):
            out[k] = v
        else:
            # list / dict -> JSON string fallback for common case
            try:
                import json
                out[k] = json.dumps(v, default=str)
            except Exception:
                pass
    return out


# --- orders ---

def _order_to_metadata(o: Order) -> dict:
    d = o.model_dump()
    d["order_date"] = o.order_date.isoformat()
    if o.estimated_delivery:
        d["estimated_delivery"] = o.estimated_delivery.isoformat()
    if o.delivered_date:
        d["delivered_date"] = o.delivered_date.isoformat()
    d["status"] = o.status.value
    d["drivetrain"] = o.drivetrain.value
    for k in ("tow_package", "autopilot", "premium_audio"):
        d[k] = int(d[k])
    return d


def upsert_orders(orders: Iterable[Order]) -> int:
    orders = list(orders)
    if not orders:
        return 0
    coll = get_orders_collection()
    embedder = get_embedder()
    docs = [o.to_search_document() for o in orders]
    embs = embedder.embed(docs)
    coll.upsert(
        ids=[o.order_id for o in orders],
        documents=docs,
        embeddings=embs,
        metadatas=[_coerce_metadata(_order_to_metadata(o)) for o in orders],
    )
    return len(orders)


def delete_order(order_id: str) -> None:
    get_orders_collection().delete(ids=[order_id])


def count_orders() -> int:
    return get_orders_collection().count()


# --- documents (free-text chunks) ---

def upsert_documents(
    chunks: Iterable[dict],
) -> int:
    """chunks: iterable of {id, text, metadata}. Returns count ingested."""
    chunks = list(chunks)
    if not chunks:
        return 0
    coll = get_documents_collection()
    embedder = get_embedder()
    texts = [c["text"] for c in chunks]
    embs = embedder.embed(texts)
    coll.upsert(
        ids=[c["id"] for c in chunks],
        documents=texts,
        embeddings=embs,
        metadatas=[_coerce_metadata(c.get("metadata", {})) for c in chunks],
    )
    return len(chunks)


def delete_document(chunk_id: str) -> None:
    get_documents_collection().delete(ids=[chunk_id])


def count_documents() -> int:
    return get_documents_collection().count()


def list_sources() -> list[dict]:
    """Distinct (source, kind, n_chunks) summary across the documents collection."""
    coll = get_documents_collection()
    if coll.count() == 0:
        return []
    # Pull everything (prototype-scale). Return groups.
    res = coll.get(include=["metadatas"])
    groups: dict[tuple, int] = {}
    for m in res["metadatas"]:
        key = (m.get("source", ""), m.get("kind", ""))
        groups[key] = groups.get(key, 0) + 1
    return [
        {"source": s, "kind": k, "chunks": n}
        for (s, k), n in sorted(groups.items(), key=lambda x: x[0])
    ]


# --- search ---

def _build_where(filters: Optional[dict]) -> Optional[dict]:
    """Translate a friendly filter dict to a chroma where clause."""
    if not filters:
        return None
    clauses = []
    for k, v in filters.items():
        if isinstance(v, dict):
            clauses.append({k: v})
        else:
            clauses.append({k: {"$eq": v}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _query_coll(coll, query_emb, top_k, filters, source_label: str) -> list[dict]:
    if coll.count() == 0:
        return []
    res = coll.query(
        query_embeddings=[query_emb],
        n_results=top_k,
        where=_build_where(filters),
        include=["documents", "metadatas", "distances"],
    )
    out: list[dict] = []
    for i, cid in enumerate(res["ids"][0]):
        sim = 1.0 - float(res["distances"][0][i])
        out.append(
            {
                "id": cid,
                "score": round(sim, 4),
                "document": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "source": source_label,
            }
        )
    return out


def search(
    query: str,
    top_k: int = 5,
    filters: Optional[dict] = None,
    sources: Optional[list[str]] = None,
) -> list[dict]:
    """Hybrid semantic + structured search across all collections.

    sources: subset of {"orders", "documents"}. None = both.
    """
    src_set = set(sources) if sources else {"orders", "documents"}
    embedder = get_embedder()
    q_emb = embedder.embed([query])[0]
    # Over-fetch per collection, then merge + trim, since each collection
    # returns its own top-k independently.
    per = max(top_k, 5)
    hits: list[dict] = []
    if "orders" in src_set:
        hits.extend(_query_coll(get_orders_collection(), q_emb, per, filters, "orders"))
    if "documents" in src_set:
        hits.extend(_query_coll(get_documents_collection(), q_emb, per, filters, "documents"))
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


# --- backwards-compat shim used by older code paths ---
def count() -> int:
    return count_orders() + count_documents()
