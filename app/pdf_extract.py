"""
PDF extraction -> text chunks for the vector store.

This module is the orchestrator. The actual table extraction work is
delegated to pluggable backends (see `app/pdf_backends/`):

  * `pdfplumber`  - always tried first; fast, good for bordered tables.
  * `camelot`     - opt-in; better for lattice and stream tables.
  * `vision`      - opt-in; renders the page to PNG and asks a vision LLM.
  * `none`        - text only, no table extraction.

Configure the order with the `PDF_TABLE_BACKENDS` env var (comma-separated),
e.g. `PDF_TABLE_BACKENDS=pdfplumber,camelot,vision`.

The orchestrator only consults the i-th backend when the previous ones
returned no tables AND the page looks tabular-ish (i.e. has enough text
but pdfplumber found no table). This keeps cost low and avoids vision
calls for pages that pdfplumber already handled.
"""
from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Protocol

import pdfplumber


# --- result types ---

@dataclass
class TableResult:
    """A table extracted from a single page, normalized to markdown."""
    markdown: str
    backend: str  # which backend produced this ("pdfplumber", "camelot", "vision")
    raw_rows: list[list[Optional[str]]] = field(default_factory=list)


@dataclass
class TextChunk:
    id: str
    text: str
    metadata: dict


# --- backend protocol ---

class TableBackend(Protocol):
    name: str

    def extract_tables(
        self,
        *,
        pdf_path: Path,
        page_index: int,  # 0-based
        page: pdfplumber.page.Page,
        page_text: str,
    ) -> list[TableResult]: ...


# --- helpers ---

def _table_to_markdown(table: list[list[Optional[str]]]) -> str:
    cleaned: list[list[str]] = []
    for row in table:
        cells = [(c or "").strip().replace("\n", " ") for c in row]
        if any(cells):
            cleaned.append(cells)
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    for r in cleaned:
        while len(r) < width:
            r.append("")
    header = cleaned[0]
    body = cleaned[1:] if len(cleaned) > 1 else []
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _clean_text(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _table_row_cells(table: list[list[Optional[str]]]) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    for row in table:
        cells = tuple(c.strip().lower() for c in row if c and c.strip())
        if cells:
            out.append(cells)
    return out


def _line_covers_row(line: str, row_cells: tuple[str, ...]) -> bool:
    if not row_cells:
        return False
    pos = 0
    low = line.lower()
    for i, cell in enumerate(row_cells):
        idx = low.find(cell, pos)
        if idx < 0:
            return False
        if i == 0:
            if idx > 0 and (low[idx - 1].isalnum()):
                return False
            end = idx + len(cell)
            if end < len(low) and low[end].isalnum():
                return False
        pos = idx + len(cell)
    return True


def _strip_table_rows_from_text(text: str, table: list[list[Optional[str]]]) -> str:
    rows = _table_row_cells(table)
    if not rows:
        return text
    out_lines: list[str] = []
    for line in text.splitlines():
        if any(_line_covers_row(line, r) for r in rows):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _looks_tabular(text: str) -> bool:
    """Heuristic: a page with little text and many short lines that
    line up is likely a borderless table. Used to decide whether to
    try fallback backends.

    This is intentionally simple. The vision backend doesn't care - it
    looks at the rendered page directly. The Camelot backend uses its
    own detection, so this is only a cheap pre-filter.
    """
    if not text:
        return False
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    # many short lines, no obvious paragraph structure
    short = sum(1 for l in lines if len(l) <= 80)
    return short / len(lines) > 0.7


# --- backend registry ---

_BACKENDS: dict[str, TableBackend] = {}


def register_backend(backend: TableBackend) -> None:
    _BACKENDS[backend.name] = backend


def _get_backend(name: str) -> Optional[TableBackend]:
    if name in _BACKENDS:
        return _BACKENDS[name]
    # Lazy import for optional backends
    if name == "camelot":
        try:
            from .pdf_backends import camelot_backend
            register_backend(camelot_backend.backend)
            return _BACKENDS[name]
        except Exception as e:
            warnings.warn(f"camelot backend unavailable: {e}")
            return None
    if name == "vision":
        try:
            from .pdf_backends import vision_backend
            register_backend(vision_backend.backend)
            return _BACKENDS[name]
        except Exception as e:
            warnings.warn(f"vision backend unavailable: {e}")
            return None
    return None


def available_backends() -> list[str]:
    return ["pdfplumber", "camelot", "vision"]


# --- pdfplumber backend (always available) ---

class _PdfplumberBackend:
    name = "pdfplumber"

    def extract_tables(self, *, pdf_path, page_index, page, page_text):
        try:
            tables = page.extract_tables() or []
        except Exception as e:
            warnings.warn(f"pdfplumber.extract_tables failed on p{page_index + 1}: {e}")
            tables = []
        results: list[TableResult] = []
        for t in tables:
            md = _table_to_markdown(t)
            if md:
                results.append(TableResult(markdown=md, backend=self.name, raw_rows=t))
        return results


register_backend(_PdfplumberBackend())


# --- orchestrator ---

def _configured_backends() -> list[str]:
    raw = os.environ.get("PDF_TABLE_BACKENDS", "pdfplumber")
    return [b.strip().lower() for b in raw.split(",") if b.strip()]


def _should_try_fallback(page_text: str, prior_tables: list[TableResult]) -> bool:
    """Decide whether to consult the next backend for this page."""
    if prior_tables:
        return False
    min_chars = int(os.environ.get("PDF_TABLE_MIN_CHARS", "80"))
    if len(page_text) < min_chars:
        return False
    return True


def _page_tables(
    *,
    pdf_path: Path,
    page_index: int,
    page: pdfplumber.page.Page,
    page_text: str,
) -> tuple[list[TableResult], str]:
    """Run configured backends in order. Return (tables, final_page_text).

    `final_page_text` is the text with rows from any extracted tables
    removed (so the table content isn't double-embedded).
    """
    backends = _configured_backends()
    tables: list[TableResult] = []
    seen_markdowns: set[str] = set()
    used_text = page_text

    for name in backends:
        if name == "none":
            return tables, used_text
        if name not in ("pdfplumber",) and not _should_try_fallback(used_text, tables):
            # pdfplumber found something OR the page has too little text;
            # no need to try a more expensive backend.
            break
        backend = _get_backend(name)
        if backend is None:
            continue
        try:
            new_tables = backend.extract_tables(
                pdf_path=pdf_path,
                page_index=page_index,
                page=page,
                page_text=used_text,
            )
        except Exception as e:
            warnings.warn(f"backend '{name}' failed on p{page_index + 1}: {e}")
            continue
        for t in new_tables:
            if t.markdown and t.markdown not in seen_markdowns:
                seen_markdowns.add(t.markdown)
                tables.append(t)
                # remove this table's rows from the text layer
                if t.raw_rows:
                    used_text = _strip_table_rows_from_text(used_text, t.raw_rows)

    return tables, used_text


def extract_pdf(
    path: str | Path,
    kind: str = "configurator_pdf",
    source_label: Optional[str] = None,
    debug_dump_dir: Optional[Path] = None,
) -> list[TextChunk]:
    """Extract chunks from a single PDF."""
    path = Path(path)
    source = source_label or path.name
    chunks: list[TextChunk] = []

    if debug_dump_dir:
        debug_dump_dir.mkdir(parents=True, exist_ok=True)

    with pdfplumber.open(path) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            page_text = _clean_text(page_text)

            tables, used_text = _page_tables(
                pdf_path=path,
                page_index=page_idx - 1,
                page=page,
                page_text=page_text,
            )
            tables_md = [t.markdown for t in tables]
            backends_used = sorted({t.backend for t in tables})

            parts: list[str] = []
            if used_text:
                parts.append(used_text)
            for i, md in enumerate(tables_md, start=1):
                backend = tables[i - 1].backend
                parts.append(f"\n[Table {i} — {backend}]\n{md}\n")

            full_text = "\n".join(parts).strip()
            if not full_text:
                continue

            meta = {
                "source": source,
                "source_path": str(path.resolve()),
                "kind": kind,
                "page": page_idx,
                "n_tables": len(tables_md),
                "n_chars": len(full_text),
                "low_text": int(len(page_text) < 20 and len(tables_md) == 0),
                "table_backends": ",".join(backends_used) if backends_used else "",
            }
            chunks.append(
                TextChunk(
                    id=f"{source}::p{page_idx}",
                    text=full_text,
                    metadata=meta,
                )
            )

            if debug_dump_dir:
                (debug_dump_dir / f"{source}::p{page_idx}.txt").write_text(full_text)

    return chunks


def extract_pdfs(
    paths: Iterable[str | Path],
    kind: str = "configurator_pdf",
    debug_dump_dir: Optional[Path] = None,
) -> list[TextChunk]:
    out: list[TextChunk] = []
    for p in paths:
        out.extend(extract_pdf(p, kind=kind, debug_dump_dir=debug_dump_dir))
    return out
