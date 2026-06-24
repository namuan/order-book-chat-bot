.PHONY: help install install-all env serve dev test screenshots clean clean-all ingest-orders sample-pdfs ingest-pdfs reset-db lint

PYTHON    ?= uv run python
SERVER    := $(PYTHON) -m app.server

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

install:  ## Install base + dev dependencies
	uv sync --extra dev

install-all:  ## Install all optional dependencies (dev + pdf-camelot)
	uv sync --extra dev --extra pdf-camelot

env:  ## Copy .env.example to .env
	@test -f .env || cp .env.example .env

# ---------------------------------------------------------------------------
# ingest sample data
# ---------------------------------------------------------------------------

ingest-orders:  ## Ingest sample orders from data/sample_orders.json
	$(PYTHON) -m scripts.ingest data/sample_orders.json

sample-pdfs:  ## Generate sample configurator PDFs in data/sample_pdfs/
	$(PYTHON) -m scripts.make_sample_pdfs

ingest-pdfs:  ## Ingest PDFs from data/sample_pdfs/
	$(PYTHON) -m scripts.ingest_pdf data/sample_pdfs

ingest-all: sample-pdfs ingest-orders ingest-pdfs  ## Generate + ingest everything

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

serve:  ## Start the FastAPI server (http://127.0.0.1:8000)
	$(SERVER)

dev:  ## Start the server with auto-reload
	uv run uvicorn app.server:app --reload --host 127.0.0.1 --port 8000

# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

test:  ## Run the test suite
	uv run pytest tests/ -v

# ---------------------------------------------------------------------------
# docs / screenshots
# ---------------------------------------------------------------------------

screenshots:  ## Regenerate light-mode screenshots and optimised JPEGs
	$(PYTHON) -m scripts.take_screenshots

# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

reset-db:  ## Delete the local ChromaDB store
	rm -rf data/chroma data/chroma_test

clean:  ## Remove Python build / cache artefacts
	rm -rf .pytest_cache __pycache__ */__pycache__ */*/__pycache__ *.egg-info

clean-all: clean reset-db  ## Also remove .venv and the ChromaDB store
	rm -rf .venv
