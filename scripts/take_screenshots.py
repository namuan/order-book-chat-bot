"""
Automated screenshot capture for the Order Guide Chat Bot UI.

Starts the server, ingests sample data, then uses Playwright to capture
screenshots of the key UI features.

Usage:
    uv run python -m scripts.take_screenshots

Output: screenshots/ (PNG originals) + assets/ (optimised JPEGs for README).
Light mode is forced so screenshots show the light theme.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Use the default (production) collections so the UI has real data.
# Explicitly unset any test overrides that may be in the environment.
for _k in ("COLLECTION_NAME", "DOCS_COLLECTION_NAME", "CHROMA_PERSIST_DIR"):
    os.environ.pop(_k, None)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
BASE_URL = "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(*args: str) -> None:
    subprocess.run([sys.executable, "-m", *args], cwd=PROJECT_ROOT, check=True)


def wait_for_server(url: str, timeout: int = 30) -> bool:
    import httpx

    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(url)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# server lifecycle
# ---------------------------------------------------------------------------

def start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.server:app",
            "--host", "127.0.0.1", "--port", "8000",
            "--log-level", "info",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


# ---------------------------------------------------------------------------
# screenshot scenarios
# ---------------------------------------------------------------------------

def take_screenshots(page):
    """Navigate the UI and capture screenshots of each feature."""

    # ---- 0. Activate light mode ----
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    page.evaluate("localStorage.setItem('theme', 'light'); window.location.reload()")
    page.wait_for_load_state("networkidle")

    # ---- 1. Home page with stats ----
    page.wait_for_timeout(1000)  # let the stats fetch finish
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "01-home-with-data.png"),
        full_page=True,
    )
    print("  01  home-with-data")

    # ---- 2. Click an example button (chat mode) ----
    example_btn = page.locator("button[data-q]").first
    example_btn.click()
    page.wait_for_timeout(3000)  # wait for answer + hits to render
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "02-chat-example.png"),
        full_page=True,
    )
    print("  02  chat-example")

    # ---- 3. Manual chat query ----
    page.fill("#q", "Which customers have premium audio in their car?")
    page.click("#go")
    page.wait_for_timeout(3000)
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "03-chat-query.png"),
        full_page=True,
    )
    print("  03  chat-query")

    # ---- 4. Switch to search tab ----
    page.click(".tab[data-mode='search']")
    page.wait_for_timeout(500)
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "04-search-tab.png"),
        full_page=True,
    )
    print("  04  search-tab")

    # ---- 5. Search results ----
    page.fill("#q", "truck for towing a horse trailer")
    page.click("#go")
    page.wait_for_timeout(3000)
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "05-search-results.png"),
        full_page=True,
    )
    print("  05  search-results")

    # ---- 6. Filters populated ----
    page.fill("#f-model", "R1T")
    page.fill("#f-status", "in_production")
    page.fill("#f-region", "CA")
    page.fill("#f-maxprice", "90000")
    page.click("#go")
    page.wait_for_timeout(3000)
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "06-filtered-search.png"),
        full_page=True,
    )
    print("  06  filtered-search")

    # ---- 7. Deselect a source pill ----
    page.click("#clear")
    page.wait_for_timeout(300)
    # Deselect "Documents" so only Orders is active
    documents_pill = page.locator(".src-pill[data-src='documents']")
    documents_pill.click()
    page.fill("#q", "Model 3")
    page.click("#go")
    page.wait_for_timeout(3000)
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "07-source-filter.png"),
        full_page=True,
    )
    print("  07  source-filter")

    # ---- 8. Empty state (clear everything) ----
    page.click("#clear")
    page.wait_for_timeout(300)
    page.screenshot(
        path=str(SCREENSHOTS_DIR / "08-empty-state.png"),
        full_page=True,
    )
    print("  08  empty-state")




# ---------------------------------------------------------------------------
# post-processing: convert PNGs → optimised JPEGs in assets/
# ---------------------------------------------------------------------------

def optimise_screenshots() -> None:
    """Convert PNG screenshots to resized, compressed JPEGs in assets/."""
    import subprocess as sp

    assets_dir = PROJECT_ROOT / "assets"
    assets_dir.mkdir(exist_ok=True)

    for png in sorted(SCREENSHOTS_DIR.glob("*.png")):
        jpg = assets_dir / f"{png.stem}.jpg"
        sp.run(
            ["sips", "-z", "720", "1024",
             "-s", "format", "jpeg",
             "-s", "formatOptions", "80",
             str(png), "--out", str(jpg)],
            capture_output=True, check=True,
        )
        size_kb = jpg.stat().st_size / 1024
        print(f"  {jpg.name}  ({size_kb:.0f} KB)")

    print(f"  -> {assets_dir}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)

    # 1. Ingest sample data
    print("--- Ingesting sample orders ---")
    _run("scripts.ingest", str(PROJECT_ROOT / "data" / "sample_orders.json"))

    print("--- Generating sample PDFs ---")
    _run("scripts.make_sample_pdfs")

    print("--- Ingesting PDFs ---")
    _run("scripts.ingest_pdf", str(PROJECT_ROOT / "data" / "sample_pdfs"))

    # 2. Start server
    print("--- Starting server ---")
    server_proc = start_server()

    try:
        if not wait_for_server(f"{BASE_URL}/health"):
            print("ERROR: Server did not become ready")
            server_proc.terminate()
            sys.exit(1)
        print("--- Server ready ---")

        # 3. Take screenshots
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            take_screenshots(page)
            browser.close()

        print("\n--- Optimising screenshots ---")
        optimise_screenshots()

        print(f"\nDone. PNG originals in: {SCREENSHOTS_DIR}")
        print(f"         Optimised JPEGs in: {PROJECT_ROOT / 'assets'}")

    finally:
        server_proc.terminate()
        server_proc.wait()
        print("--- Server stopped ---")


if __name__ == "__main__":
    main()
