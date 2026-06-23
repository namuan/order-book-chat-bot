"""
Ingest PDFs into the document collection.

Usage:
    python -m scripts.ingest_pdf path/to/file.pdf
    python -m scripts.ingest_pdf path/to/folder
    python -m scripts.ingest_pdf a.pdf b.pdf c.pdf --kind order_book

PDF table extraction is pluggable. By default we use pdfplumber; pass
`--fallback camelot` and/or `--fallback vision` to enable additional
backends. They are tried in order, only when pdfplumber returned no
tables AND the page looks tabular-ish (controlled by PDF_TABLE_MIN_CHARS).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.pdf_extract import extract_pdf
from app.store import count_documents, upsert_documents


_BACKEND_ALIASES = {
    "pdfplumber": "pdfplumber",
    "camelot": "camelot",
    "vision": "vision",
    "none": "none",
}


def _expand_paths(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out.extend(sorted(path.glob("**/*.pdf")))
        else:
            out.append(path)
    return [p for p in out if p.exists() and p.suffix.lower() == ".pdf"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest PDFs into the document store.")
    ap.add_argument("paths", nargs="+", help="PDF files or directories (recursive).")
    ap.add_argument("--kind", default="configurator_pdf",
                    help="Tag stored in metadata (default: configurator_pdf).")
    ap.add_argument(
        "--fallback",
        action="append",
        choices=list(_BACKEND_ALIASES),
        help=(
            "Add a backend to the chain. Pass multiple times for an ordered "
            "chain (e.g. `--fallback camelot --fallback vision`). The chain "
            "is appended to whatever PDF_TABLE_BACKENDS is set to (default: "
            "pdfplumber). Use the env var alone to define the full chain."
        ),
    )
    ap.add_argument(
        "--debug",
        type=Path,
        default=None,
        help="If set, write each page's extracted text to this directory.",
    )
    args = ap.parse_args()

    if args.fallback:
        # `--fallback X` *appends* X to the configured chain (additive).
        # To define the chain from scratch, set PDF_TABLE_BACKENDS and
        # don't pass --fallback.
        existing = os.environ.get("PDF_TABLE_BACKENDS", "pdfplumber")
        chain = [b for b in existing.split(",") if b]
        for fb in args.fallback:
            if fb not in chain:
                chain.append(fb)
        os.environ["PDF_TABLE_BACKENDS"] = ",".join(chain)

    files = _expand_paths(args.paths)
    if not files:
        print("no PDF files found", file=sys.stderr)
        return 1

    before = count_documents()
    total_chunks = 0
    debug_dir = args.debug
    for f in files:
        chunks = extract_pdf(f, kind=args.kind, debug_dump_dir=debug_dir)
        n = upsert_documents(
            {"id": c.id, "text": c.text, "metadata": c.metadata} for c in chunks
        )
        # per-file summary: union of backends actually used
        used: set[str] = set()
        for c in chunks:
            tb = c.metadata.get("table_backends", "")
            if tb:
                used.update(tb.split(","))
        bs = ",".join(sorted(used)) or "none"
        print(f"  {f.name}: {n} chunk(s) [tables via: {bs}]")
        total_chunks += n

    after = count_documents()
    print(f"ingested {total_chunks} chunk(s) from {len(files)} file(s) "
          f"(documents collection: {before} -> {after})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
