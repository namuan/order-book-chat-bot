"""
Ingest a JSON file of orders into the vector store.

Usage:
    python -m scripts.ingest path/to/orders.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.models import Order
from app.store import count, upsert_orders


def load_orders(path: Path) -> list[Order]:
    raw = json.loads(path.read_text())
    return [Order.model_validate(r) for r in raw]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m scripts.ingest <path-to-orders.json>")
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"file not found: {path}")
        return 1
    before = count()
    orders = load_orders(path)
    n = upsert_orders(orders)
    after = count()
    print(f"loaded {n} orders from {path} (collection: {before} -> {after})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
