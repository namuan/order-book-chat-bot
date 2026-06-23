"""
Generate a sample PDF with a borderless table that's hard for pdfplumber
to detect. This is the kind of layout Camelot's "stream" mode or a
vision LLM is meant to handle.

We build it from plain text rendered as a Table with invisible borders.
The text on the page will look tabular, but pdfplumber.extract_tables()
will not detect it (no cell separators), so the fallback chain kicks in.
"""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.lib import colors


def _build_pdf(out: Path) -> None:
    """Render a 'pricing comparison' page with whitespace-aligned columns
    but no visible borders. pdfplumber.extract_tables() will skip this;
    Camelot stream mode (and vision LLMs) should pick it up."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER, title="Borderless Configurator Pricing"
    )
    story: list = []
    story.append(Paragraph("Configurator Pricing Comparison", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "The following table shows dealer-installed option pricing for the "
        "Model Y Performance build. All values are USD.",
        styles["BodyText"],
    ))
    story.append(Spacer(1, 14))

    # Borderless table: no GRID, no BACKGROUND, no inner lines.
    rows = [
        ["Option", "Description", "Price"],
        ["Paint", "Midnight Silver Metallic", "1,000"],
        ["Paint", "Pearl White Multi-Coat", "2,000"],
        ["Wheels", '21" Uberturbine', "2,000"],
        ["Interior", "White Premium Seats", "1,500"],
        ["Tow Hitch", "Class III Tow Package", "1,000"],
        ["FSD", "Full Self-Driving (Supervised)", "8,000"],
        ["Premium Audio", "22-Speaker Audio System", "0"],
        ["Subtotal", "Options", "15,500"],
    ]
    t = Table(rows, colWidths=[110, 260, 90])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Header row in bold but no visible separator
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        # Subtotal row in bold
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Notes: Pricing excludes destination, taxes, and any state EV "
        "incentives. Tow hitch requires the premium audio package.",
        styles["BodyText"],
    ))
    doc.build(story)


def main() -> int:
    out_dir = Path(os.environ.get("SAMPLE_PDF_DIR", "data/sample_pdfs"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "borderless-pricing.pdf"
    _build_pdf(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
