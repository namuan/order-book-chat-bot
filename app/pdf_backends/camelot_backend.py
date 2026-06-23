"""
Camelot backend for table extraction.

Camelot is good at:
  * `lattice` mode - tables with visible borders (lines)
  * `stream`  mode - tables inferred from whitespace (borderless)

We try lattice first, then fall back to stream if lattice returns
nothing AND the page looks tabular-ish.

Requires:
  pip install camelot-py
  # plus Ghostscript on the system PATH for lattice mode

If camelot isn't importable, the backend returns an empty list and the
caller falls through to the next configured backend.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pdfplumber

from ..pdf_extract import TableResult, _table_to_markdown


class _CamelotBackend:
    name = "camelot"

    def extract_tables(
        self,
        *,
        pdf_path: Path,
        page_index: int,
        page: pdfplumber.page.Page,
        page_text: str,
    ) -> list[TableResult]:
        try:
            import camelot  # type: ignore
        except Exception as e:
            warnings.warn(f"camelot not installed: {e}")
            return []

        results: list[TableResult] = []
        page_1based = str(page_index + 1)

        # Try lattice first (bordered tables). If it raises (e.g. ghostscript
        # missing), we silently skip and try stream.
        try:
            lattice_tables = camelot.read_pdf(
                str(pdf_path), pages=page_1based, flavor="lattice"
            )
        except Exception as e:
            warnings.warn(f"camelot lattice failed on p{page_1based}: {e}")
            lattice_tables = []

        for t in lattice_tables:
            df = t.df
            header = [str(c) for c in df.columns.tolist()]
            body = [_normalize_row(r) for r in df.values.tolist()]
            table = _rotate_header([header] + body)
            if len(table) < 2:
                continue
            md = _table_to_markdown(table)
            if md:
                results.append(TableResult(markdown=md, backend=self.name, raw_rows=table))

        # If lattice got nothing and the page looks tabular-ish, try stream
        if not results and self._looks_tabular(page_text):
            try:
                stream_tables = camelot.read_pdf(
                    str(pdf_path),
                    pages=page_1based,
                    flavor="stream",
                    # Generous defaults; tune in the field.
                    row_tol=2,
                    column_tol=2,
                )
            except Exception as e:
                warnings.warn(f"camelot stream failed on p{page_1based}: {e}")
                stream_tables = []

            for t in stream_tables:
                df = t.df
                header = [str(c) for c in df.columns.tolist()]
                body = [_normalize_row(r) for r in df.values.tolist()]
                table = _rotate_header([header] + body)
                if len(table) < 2:
                    continue
                md = _table_to_markdown(table)
                if md:
                    results.append(
                        TableResult(markdown=md, backend=self.name, raw_rows=table)
                    )

        return results

    @staticmethod
    def _looks_tabular(text: str) -> bool:
        if not text:
            return False
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) < 4:
            return False
        short = sum(1 for l in lines if len(l) <= 80)
        return short / len(lines) > 0.7


def _normalize_row(row) -> list[str | None]:
    """Coerce a Camelot DataFrame row to the (str|None) shape the
    orchestrator expects. NaN/None -> None, everything else -> str."""
    import math
    out: list[str | None] = []
    for c in row:
        if c is None:
            out.append(None)
        elif isinstance(c, float) and math.isnan(c):
            out.append(None)
        elif c == "":
            out.append(None)
        else:
            out.append(str(c))
    return out


def _rotate_header(table: list[list[str | None]]) -> list[list[str | None]]:
    """Post-process a Camelot table.

    Camelot's stream mode often puts a placeholder header (`0/1/2`) on
    top and folds the real data into the rows, OR folds a paragraph
    sentence into the first row. We try to detect the real header row
    and rotate the table so it sits at the top.

    Rules (in order):
      1. Drop fully-empty rows.
      2. Drop a leading row of pure-digit placeholders (e.g. `0/1/2`).
      3. If the first row doesn't look like a header but a later row
         does, rotate the table to start at that row.
    """
    rows = [r for r in table if any(c for c in r)]
    if not rows:
        return rows

    def _looks_like_header(row) -> bool:
        if not row or not all(row):
            return False
        for c in row:
            if not c or len(c) > 30:
                return False
            if c.endswith((".", "?", "!", ",", ":")):
                return False
        return True

    # Drop pure-digit placeholder header (Camelot's stream default)
    if all(c is not None and c.isdigit() for c in rows[0]):
        rows = rows[1:]

    # If the first remaining row isn't a header but a later one is, rotate.
    if rows and not _looks_like_header(rows[0]):
        for i in range(1, len(rows)):
            if _looks_like_header(rows[i]):
                rows = rows[i:]
                break
    return rows


backend = _CamelotBackend()


backend = _CamelotBackend()
