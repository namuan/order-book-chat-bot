"""
Generate a small set of sample configurator PDFs for testing the
PDF ingestion pipeline.

Usage:
    python -m scripts.make_sample_pdfs
"""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


SAMPLES = [
    {
        "filename": "rivian-r1t-avery-chen.pdf",
        "title": "Rivian R1T - Configuration Summary",
        "customer": "Avery Chen",
        "order_id": "ORD-000001",
        "paragraphs": [
            "Customer: Avery Chen (CA). Reservation placed 2026-02-14.",
            "Vehicle is intended for a Yellowstone road trip in September; "
            "customer emphasized towing capacity and range.",
        ],
        "config": [
            ["Option", "Value"],
            ["Model", "R1T"],
            ["Trim", "Adventure"],
            ["Exterior", "Launch Green"],
            ["Interior", "Black Mountain"],
            ["Wheels", '20" All-Terrain'],
            ["Drivetrain", "Quad-Motor"],
            ["Battery", "Max Pack"],
            ["Tow Package", "Yes"],
            ["Premium Audio", "Yes"],
            ["Driver Assist", "No"],
            ["MSRP", "$87,900"],
            ["Deposit", "$1,000"],
            ["Status", "In Production"],
            ["Est. Delivery", "2026-08-22"],
        ],
        "notes_table": [
            ["Field", "Detail"],
            ["Reservation Date", "2026-02-14"],
            ["Lead Time", "~6 months"],
            ["Delivery Hub", "San Francisco, CA"],
        ],
    },
    {
        "filename": "ford-f150-lightning-marcus-hill.pdf",
        "title": "Ford F-150 Lightning - Configuration Summary",
        "customer": "Marcus Hill",
        "order_id": "ORD-000003",
        "paragraphs": [
            "Customer: Marcus Hill (TX). Fleet manager for a ranch in central Texas.",
            "Primary use case: towing a horse trailer weekly. Needs Pro Power Onboard.",
        ],
        "config": [
            ["Option", "Value"],
            ["Model", "F-150 Lightning"],
            ["Trim", "Platinum"],
            ["Exterior", "Antimatter Blue"],
            ["Interior", "Black"],
            ["Wheels", '22" Bright Machined'],
            ["Drivetrain", "AWD"],
            ["Battery", "Extended Range"],
            ["Tow Package", "Yes"],
            ["Tow Capacity", "10,000 lbs"],
            ["Premium Audio", "Yes"],
            ["Driver Assist", "Yes (BlueCruise)"],
            ["MSRP", "$91,495"],
            ["Deposit", "$500"],
            ["Status", "In Transit"],
            ["Est. Delivery", "2026-06-30"],
        ],
        "notes_table": None,
    },
    {
        "filename": "tesla-model3-priya-natarajan.pdf",
        "title": "Tesla Model 3 - Configuration Summary",
        "customer": "Priya Natarajan",
        "order_id": "ORD-000002",
        "paragraphs": [
            "Customer: Priya Natarajan (NY). Daily commuter in the Hudson Valley.",
            "Loves the glass roof and minimalist interior. Range over performance.",
        ],
        "config": [
            ["Option", "Value"],
            ["Model", "Model 3"],
            ["Trim", "Long Range"],
            ["Exterior", "Pearl White"],
            ["Interior", "Black"],
            ["Wheels", '19" Sport'],
            ["Drivetrain", "AWD"],
            ["Battery", "Long Range"],
            ["Tow Package", "No"],
            ["Autopilot", "Yes"],
            ["Premium Audio", "Yes"],
            ["MSRP", "$47,240"],
            ["Deposit", "$250"],
            ["Status", "Delivered"],
            ["Delivered", "2026-01-12"],
        ],
        "notes_table": None,
    },
]


def _build_pdf(out: Path, sample: dict) -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, title=sample["title"])
    story: list = []
    story.append(Paragraph(sample["title"], styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Order: {sample['order_id']}", styles["Heading3"]))
    story.append(Spacer(1, 8))
    for p in sample["paragraphs"]:
        story.append(Paragraph(p, styles["BodyText"]))
        story.append(Spacer(1, 6))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Configuration", styles["Heading2"]))
    config = Table(sample["config"], colWidths=[180, 280])
    config.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9ca3af")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f3f4f6")]),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(config)
    if sample.get("notes_table"):
        story.append(Spacer(1, 14))
        story.append(Paragraph("Delivery Details", styles["Heading2"]))
        notes = Table(sample["notes_table"], colWidths=[180, 280])
        notes.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9ca3af")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
        ]))
        story.append(notes)
    doc.build(story)


def main() -> int:
    out_dir = Path(os.environ.get("SAMPLE_PDF_DIR", "data/sample_pdfs"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in SAMPLES:
        path = out_dir / s["filename"]
        _build_pdf(path, s)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
