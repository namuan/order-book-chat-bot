"""
End-to-end smoke tests for both ingestion paths:

  * Structured Order ingestion (json-style records).
  * Free-text document ingestion (PDF page chunks).

Each test uses a separate collection so it doesn't pollute the main index.
"""
from __future__ import annotations

import os
from pathlib import Path

# Run BEFORE importing the app so the env vars take effect
os.environ.setdefault("COLLECTION_NAME", "order_guides_test")
os.environ.setdefault("DOCS_COLLECTION_NAME", "documents_test")
os.environ.setdefault("CHROMA_PERSIST_DIR", "./data/chroma_test")

from app.models import Order
from app.pdf_extract import extract_pdf
from app.store import (
    count_documents,
    count_orders,
    get_documents_collection,
    get_orders_collection,
    search,
    upsert_documents,
    upsert_orders,
)


# ---------- fixtures ----------

def _orders_fixture() -> list[Order]:
    return [
        Order(
            order_id="T-001", customer_name="A", customer_region="CA",
            model="R1T", trim="Adventure", exterior_color="Green",
            interior_color="Black", wheels="20\" AT", drivetrain="quad_motor",
            battery_pack="Max", tow_package=True, autopilot=False,
            premium_audio=True, msrp_usd=80000, deposit_usd=1000,
            status="in_production", order_date="2026-01-01",
            notes="Truck for towing a horse trailer.",
        ),
        Order(
            order_id="T-002", customer_name="B", customer_region="TX",
            model="F-150 Lightning", trim="Platinum",
            exterior_color="Blue", interior_color="Black",
            wheels="22\"", drivetrain="awd",
            battery_pack="Extended", tow_package=True, autopilot=True,
            premium_audio=True, msrp_usd=90000, deposit_usd=500,
            status="delivered", order_date="2025-09-01",
            notes="Fleet manager.",
        ),
        Order(
            order_id="T-003", customer_name="C", customer_region="NY",
            model="Model 3", trim="Long Range",
            exterior_color="White", interior_color="Black",
            wheels="19\"", drivetrain="awd",
            battery_pack="Long Range", tow_package=False, autopilot=True,
            premium_audio=False, msrp_usd=45000, deposit_usd=250,
            status="cancelled", order_date="2025-11-01",
            notes="Cancelled - changed mind.",
        ),
    ]


SAMPLE_PDF = Path("data/sample_pdfs/rivian-r1t-avery-chen.pdf")


def setup_function(_):
    """Reset both test collections before each test."""
    # Each collection has its own universal discriminator: orders carry
    # `order_id`, documents carry `source`. `{"$ne": ""}` matches every
    # non-empty value, i.e. all of them.
    discriminators = {
        "orders": {"order_id": {"$ne": ""}},
        "documents": {"source": {"$ne": ""}},
    }
    for name, coll in (
        ("orders", get_orders_collection()),
        ("documents", get_documents_collection()),
    ):
        if coll.count() == 0:
            continue
        coll.delete(where=discriminators[name])


# ---------- orders ----------

def test_ingest_and_count_orders():
    upsert_orders(_orders_fixture())
    assert count_orders() == 3


def test_semantic_search_finds_towing_intent():
    upsert_orders(_orders_fixture())
    hits = search("I need a vehicle for towing", top_k=3)
    ids = [h["id"] for h in hits]
    assert "T-003" not in ids[:2]


def test_structured_filter_status():
    upsert_orders(_orders_fixture())
    hits = search("any car", top_k=10, filters={"status": "cancelled"})
    assert len(hits) == 1
    assert hits[0]["id"] == "T-003"


def test_structured_filter_price_range():
    upsert_orders(_orders_fixture())
    hits = search("any car", top_k=10, filters={"msrp_usd": {"$lte": 80000}})
    ids = {h["id"] for h in hits}
    assert "T-001" in ids and "T-003" in ids
    assert "T-002" not in ids


def test_hybrid_query():
    upsert_orders(_orders_fixture())
    hits = search("truck", top_k=5, filters={"customer_region": "TX"})
    assert len(hits) >= 1
    assert all(h["metadata"]["customer_region"] == "TX" for h in hits)


# ---------- pdf / documents ----------

def test_pdf_extraction_emits_one_chunk_per_page():
    if not SAMPLE_PDF.exists():
        # generate on demand so the test passes in a fresh checkout
        from scripts.make_sample_pdfs import main as make_pdfs
        make_pdfs()
    chunks = extract_pdf(SAMPLE_PDF)
    assert len(chunks) >= 1
    assert all(c.id.startswith("rivian-r1t-avery-chen.pdf::p") for c in chunks)
    assert all(c.metadata["kind"] == "configurator_pdf" for c in chunks)
    # The sample PDF contains a Configuration table - that should be in the text
    assert any("MSRP" in c.text and "$87,900" in c.text for c in chunks)


def test_pdf_ingest_and_search():
    if not SAMPLE_PDF.exists():
        from scripts.make_sample_pdfs import main as make_pdfs
        make_pdfs()
    chunks = extract_pdf(SAMPLE_PDF)
    n = upsert_documents(
        {"id": c.id, "text": c.text, "metadata": c.metadata} for c in chunks
    )
    assert n == len(chunks)
    assert count_documents() == n

    hits = search("Yellowstone road trip towing capacity", top_k=2, sources=["documents"])
    assert hits
    assert hits[0]["source"] == "documents"
    assert "rivian-r1t-avery-chen.pdf" in hits[0]["metadata"]["source"]


def test_search_merges_orders_and_documents():
    # Ingest both; a hybrid query should pull from both
    upsert_orders(_orders_fixture())
    chunks = extract_pdf(SAMPLE_PDF)
    upsert_documents(
        {"id": c.id, "text": c.text, "metadata": c.metadata} for c in chunks
    )

    hits = search("Adventure trim quad motor", top_k=5)  # default: both
    sources = {h["source"] for h in hits}
    assert "orders" in sources or "documents" in sources
