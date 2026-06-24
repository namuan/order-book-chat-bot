"""
FastAPI app exposing the order-guide vector search + chat (RAG) endpoints.

Searches are unified: the chat and search endpoints query BOTH the
structured Order collection and the free-text document collection, merge
by similarity, and return a single ranked list.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .llm import answer
from .models import Order
from .store import (
    count_documents,
    count_orders,
    delete_document,
    delete_order,
    list_sources,
    search,
    upsert_documents,
    upsert_orders,
)

load_dotenv()

app = FastAPI(title="Order Book Vector Search", version="0.2.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- request / response schemas ---

class BulkIngest(BaseModel):
    orders: list[Order]


class DocumentChunk(BaseModel):
    id: str = Field(..., description="Stable id, e.g. 'my-pdf::p2'")
    text: str
    metadata: dict = Field(default_factory=dict)


class BulkDocuments(BaseModel):
    documents: list[DocumentChunk]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=50)
    filters: Optional[dict] = None
    sources: Optional[list[str]] = Field(
        default=None,
        description="Subset of {'orders', 'documents'}. Default: both.",
    )


class SearchHit(BaseModel):
    id: str
    score: float
    document: str
    metadata: dict
    source: str  # "orders" | "documents"


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=20)
    filters: Optional[dict] = None
    sources: Optional[list[str]] = None


class ChatResponse(BaseModel):
    question: str
    answer: str
    used_model: str
    hits: list[SearchHit]


# --- routes ---

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "indexed_orders": count_orders(),
        "indexed_documents": count_documents(),
    }


@app.get("/stats")
def stats() -> dict:
    return {
        "indexed_orders": count_orders(),
        "indexed_documents": count_documents(),
        "document_sources": list_sources(),
    }


# Orders
@app.post("/orders", status_code=201)
def add_order(order: Order) -> dict:
    upsert_orders([order])
    return {"ingested": 1, "order_id": order.order_id}


@app.post("/orders/bulk", status_code=201)
def add_orders_bulk(body: BulkIngest) -> dict:
    n = upsert_orders(body.orders)
    return {"ingested": n}


@app.delete("/orders/{order_id}", status_code=204)
def remove_order(order_id: str) -> None:
    delete_order(order_id)


# Documents (free-text chunks, e.g. from PDFs)
@app.post("/documents", status_code=201)
def add_documents(body: BulkDocuments) -> dict:
    chunks = [c.model_dump() for c in body.documents]
    n = upsert_documents(chunks)
    return {"ingested": n}


@app.delete("/documents/{chunk_id}", status_code=204)
def remove_document(chunk_id: str) -> None:
    delete_document(chunk_id)


# Search & chat
@app.post("/search", response_model=SearchResponse)
def search_endpoint(req: SearchRequest) -> SearchResponse:
    try:
        raw = search(
            req.query, top_k=req.top_k, filters=req.filters, sources=req.sources
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"search failed: {e}")
    return SearchResponse(query=req.query, hits=[SearchHit(**h) for h in raw])


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest) -> ChatResponse:
    try:
        hits = search(
            req.question, top_k=req.top_k, filters=req.filters, sources=req.sources
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"search failed: {e}")
    resp = answer(req.question, hits)
    return ChatResponse(
        question=req.question,
        answer=resp.text,
        used_model=resp.used_model,
        hits=[SearchHit(**h) for h in hits],
    )


def main() -> None:
    import uvicorn
    uvicorn.run(
        "app.server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
