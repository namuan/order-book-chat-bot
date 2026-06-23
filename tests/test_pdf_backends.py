"""
Tests for the pluggable PDF table extraction.

Covers:
  * pdfplumber backend (default) on bordered tables.
  * pdfplumber backend misses a borderless table.
  * Camelot backend recovers a borderless table.
  * Orchestrator respects the PDF_TABLE_BACKENDS env var.
  * Vision backend registers and is invokable (mocked - no real API call).
"""
from __future__ import annotations

import os
from pathlib import Path

# Run BEFORE importing the app so the env vars take effect
os.environ.setdefault("COLLECTION_NAME", "order_books_test")
os.environ.setdefault("DOCS_COLLECTION_NAME", "documents_test")
os.environ.setdefault("CHROMA_PERSIST_DIR", "./data/chroma_test")
# Default to pdfplumber for the whole module; individual tests override.
os.environ.setdefault("PDF_TABLE_BACKENDS", "pdfplumber")

import pytest

from app.pdf_extract import (
    _configured_backends,
    extract_pdf,
)


BORDERED = Path("data/sample_pdfs/rivian-r1t-avery-chen.pdf")
BORDERLESS = Path("data/sample_pdfs/borderless-pricing.pdf")


@pytest.fixture(autouse=True)
def _isolate_backends(monkeypatch):
    """Each test gets a fresh PDF_TABLE_BACKENDS value plus a clean registry."""
    # Default; tests can override via monkeypatch.setenv
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber")
    # Drop any backends that previous tests may have registered.
    from app.pdf_extract import _BACKENDS
    for name in ("camelot", "vision"):
        _BACKENDS.pop(name, None)
    yield


def _ensure_samples() -> None:
    if not BORDERED.exists() or not BORDERLESS.exists():
        from scripts.make_sample_pdfs import main as make_default
        from scripts.make_borderless_pdf import main as make_borderless
        make_default()
        make_borderless()


def test_pdfplumber_finds_bordered_table(monkeypatch):
    _ensure_samples()
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber")
    chunks = extract_pdf(BORDERED)
    assert chunks[0].metadata["n_tables"] >= 1
    assert chunks[0].metadata["table_backends"] == "pdfplumber"


def test_pdfplumber_misses_borderless_table(monkeypatch):
    _ensure_samples()
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber")
    chunks = extract_pdf(BORDERLESS)
    assert chunks[0].metadata["n_tables"] == 0
    # data should still be in the text layer
    assert "1,000" in chunks[0].text or "1000" in chunks[0].text


def test_camelot_recovers_borderless_table(monkeypatch):
    _ensure_samples()
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber,camelot")
    chunks = extract_pdf(BORDERLESS)
    meta = chunks[0].metadata
    assert meta["n_tables"] >= 1
    assert "camelot" in meta["table_backends"]
    # the recovered table should include the real header
    assert "Option" in chunks[0].text
    assert "Description" in chunks[0].text
    assert "Price" in chunks[0].text


def test_camelot_skipped_when_pdfplumber_succeeds(monkeypatch):
    """If pdfplumber finds a table, camelot should not be consulted."""
    _ensure_samples()
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber,camelot")
    chunks = extract_pdf(BORDERED)
    assert chunks[0].metadata["table_backends"] == "pdfplumber"


def test_configured_backends_parses_csv(monkeypatch):
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber, camelot ,vision")
    backs = _configured_backends()
    assert backs == ["pdfplumber", "camelot", "vision"]


def test_camelot_unavailable_returns_empty(monkeypatch):
    """If camelot returns nothing, the orchestrator should not crash."""
    _ensure_samples()
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "pdfplumber,camelot")

    class _FakeBackend:
        name = "camelot"
        def extract_tables(self, **kw):
            return []

    # Replace the registered camelot backend with a stub that returns nothing
    from app.pdf_extract import _BACKENDS
    _BACKENDS["camelot"] = _FakeBackend()  # type: ignore[assignment]
    chunks = extract_pdf(BORDERLESS)
    # Should not raise; pdfplumber missed, camelot returned nothing,
    # so no table - but the chunk should still exist with text.
    assert chunks[0].metadata["n_tables"] == 0


def test_vision_backend_registers_and_parses_response(monkeypatch):
    """Mock the LLM call to verify the vision backend can return tables."""
    from app.pdf_backends import vision_backend as vb

    def fake_render(*a, **kw) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # minimal header

    def fake_call(png: bytes) -> str:
        return '{"tables": ["| A | B |\\n| --- | --- |\\n| 1 | 2 |\\n| 3 | 4 |"]}'

    monkeypatch.setattr(vb, "_render_page_png", fake_render)
    monkeypatch.setattr(vb, "_call_openai", fake_call)
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "vision")
    monkeypatch.setenv("PDF_VISION_PROVIDER", "openai")

    # Force registration by accessing via the orchestrator's lazy import
    from app.pdf_extract import _BACKENDS
    _BACKENDS["vision"] = vb.backend

    chunks = extract_pdf(BORDERED)
    assert any("vision" in c.metadata.get("table_backends", "") for c in chunks)


def test_vision_handles_malformed_response(monkeypatch):
    """If the LLM returns garbage, the backend should not crash."""
    from app.pdf_backends import vision_backend as vb

    monkeypatch.setattr(vb, "_render_page_png", lambda *a, **kw: b"\x89PNG")
    monkeypatch.setattr(vb, "_call_openai", lambda png: "not json at all")
    monkeypatch.setenv("PDF_TABLE_BACKENDS", "vision")

    from app.pdf_extract import _BACKENDS
    _BACKENDS["vision"] = vb.backend

    chunks = extract_pdf(BORDERED)
    # No tables, but extraction didn't crash
    assert all(c.metadata.get("table_backends", "") in ("", "vision") for c in chunks)
