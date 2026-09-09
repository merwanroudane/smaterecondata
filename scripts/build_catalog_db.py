#!/usr/bin/env python3
"""Build the SQLite catalogue used by instant search.

The bundled provider metadata is JSON, which is convenient to ship and refresh
but expensive to search: loading it into an in-memory index measured 393 MB for
~44k series, and grows linearly. This script converts it once into an FTS5
database so a query costs kilobytes regardless of catalogue size.

Run it at deploy time (Render build command) and after any catalogue refresh:

    python scripts/build_catalog_db.py

    python scripts/fetch_open_catalog.py && python scripts/build_catalog_db.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.smatecondata.catalog.store import (  # noqa: E402
    DB_PATH,
    build_database,
    get_catalog_store,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="output database path")
    parser.add_argument("--stats", action="store_true",
                        help="report the existing database without rebuilding")
    args = parser.parse_args()

    store = get_catalog_store()

    if args.stats:
        print(f"database : {store.db_path}")
        print(f"series   : {store.count():,}")
        for provider, count in store.provider_counts().items():
            print(f"   {provider:14} {count:>7,}")
        return 0

    target = args.db or DB_PATH
    print(f"building {target} ...", flush=True)
    started = time.time()
    inserted = build_database(db_path=target)
    elapsed = time.time() - started

    size_mb = target.stat().st_size / 1048576 if target.exists() else 0.0
    print(f"indexed {inserted:,} series in {elapsed:.1f}s")
    print(f"database size: {size_mb:.1f} MB")
    print("\nInstant search now reads from this database instead of loading the "
          "catalogue into memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
