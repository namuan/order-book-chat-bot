"""
Vision-LLM backend for table extraction.

For pages where rule-based extraction failed, render the page to PNG
(pypdfium2) and ask a vision-capable LLM to return the tables as
markdown. The LLM is told to return ONLY a JSON object: {"tables": [...]}
where each entry is a markdown table string. We parse and validate.

Providers supported (env var PDF_VISION_PROVIDER):

  * openai    - chat/completions with image_url content parts
                Models: gpt-4o, gpt-4o-mini (default)
  * anthropic - messages API with image content blocks
                Models: claude-3-5-sonnet-latest (default)

If neither provider is configured, the backend returns an empty list
and the orchestrator falls through. The backend is intentionally
skipped on pages that don't look tabular-ish (the orchestrator
enforces this via PDF_TABLE_MIN_CHARS).
"""
from __future__ import annotations

import base64
import json
import os
import re
import warnings
from io import BytesIO
from pathlib import Path

import pdfplumber

from ..pdf_extract import TableResult, _table_to_markdown


PROMPT = """You are a precise table extractor. Look at this PDF page image
and return a JSON object with one key, "tables", whose value is a list
of markdown-formatted tables found on the page. Use the FIRST row of
each table as the header. Use empty cells for missing data. If there
are no tables on the page, return {"tables": []}.

Do not include any commentary, prose, or markdown fences. Respond with
only the JSON object."""


def _render_page_png(pdf_path: Path, page_index: int, scale: float = 2.0) -> bytes:
    """Render a single page to PNG bytes using pypdfium2."""
    import pypdfium2 as pdfium  # type: ignore

    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[page_index]
    bitmap = page.render(scale=scale)
    pil = bitmap.to_pil()
    buf = BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def _strip_markdown_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _parse_tables_json(text: str) -> list[list[str | None]]:
    """Parse the LLM's JSON into a list of table-row-lists.

    A "table" in the response is a markdown string. We re-parse the
    markdown to recover rows so the orchestrator can dedupe and
    strip-then-reinsert. If markdown parsing fails, we keep the raw
    string as a single "row" and let the orchestrator treat it as
    opaque markdown.
    """
    try:
        obj = json.loads(_strip_markdown_fence(text))
        tables = obj.get("tables", [])
    except Exception:
        return []
    out: list[list[str | None]] = []
    for md in tables:
        if not isinstance(md, str):
            continue
        rows = _markdown_table_to_rows(md)
        if rows:
            out.append(rows)
    return out


def _markdown_table_to_rows(md: str) -> list[list[str | None]]:
    lines = [l.strip() for l in md.strip().splitlines() if l.strip()]
    if not lines or "|" not in lines[0]:
        return []
    rows: list[list[str | None]] = []
    for line in lines:
        # Skip the separator row (| --- | --- |)
        if re.match(r"^\|\s*-+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells or None)
    # Filter fully-empty rows
    return [r for r in rows if any(c for c in r)]


# --- providers ---

def _call_openai(png: bytes) -> str:
    import httpx
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("PDF_VISION_MODEL", "gpt-4o-mini")
    b64 = base64.b64encode(png).decode("ascii")
    r = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 4096,
        },
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(png: bytes) -> str:
    import httpx
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = os.environ.get("PDF_VISION_MODEL", "claude-3-5-sonnet-latest")
    b64 = base64.b64encode(png).decode("ascii")
    r = httpx.post(
        f"{base}/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        },
        timeout=120.0,
    )
    r.raise_for_status()
    content = r.json().get("content", [])
    parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(parts)


class _VisionBackend:
    name = "vision"

    def extract_tables(
        self,
        *,
        pdf_path: Path,
        page_index: int,
        page: pdfplumber.page.Page,
        page_text: str,
    ) -> list[TableResult]:
        provider = os.environ.get("PDF_VISION_PROVIDER", "openai").lower()
        try:
            png = _render_page_png(pdf_path, page_index)
        except Exception as e:
            warnings.warn(f"vision render failed on p{page_index + 1}: {e}")
            return []

        try:
            if provider == "anthropic":
                raw = _call_anthropic(png)
            else:
                raw = _call_openai(png)
        except Exception as e:
            warnings.warn(f"vision LLM call failed on p{page_index + 1}: {e}")
            return []

        tables = _parse_tables_json(raw)
        results: list[TableResult] = []
        for rows in tables:
            md = _table_to_markdown(rows)
            if md:
                results.append(TableResult(markdown=md, backend=self.name, raw_rows=rows))
        return results


backend = _VisionBackend()
