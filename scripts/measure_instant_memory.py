#!/usr/bin/env python3
"""Measure peak memory for the query that exhausted the Render free instance.

    POST /api/v1/instant-dataset  {"query": "Inflation in Algeria from 2000 to 2025"}

Reports Python heap peak via tracemalloc (portable) and process RSS where the
platform exposes it. Run before and after an architectural change to confirm
the search path still fits inside 512 MB.

    python scripts/measure_instant_memory.py
    python scripts/measure_instant_memory.py --offline   # no provider calls
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import os
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("JWT_SECRET", "measurement-only-not-a-secret")

QUERY = "Inflation in Algeria from 2000 to 2025"
RENDER_LIMIT_MB = 512


def rss_mb() -> float:
    """Process RSS in MB, or 0.0 when the platform does not expose it."""
    try:
        with open("/proc/self/statm") as handle:
            return int(handle.read().split()[1]) * 4096 / 1048576
    except (OSError, IndexError, ValueError):
        pass
    try:
        import ctypes
        import ctypes.wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        if ok:
            return counters.PeakWorkingSetSize / 1048576
    except Exception:
        pass
    return 0.0


def report(label: str, current: float, peak: float, rss: float) -> None:
    suffix = f"   rss={rss:6.1f} MB" if rss else ""
    print(f"  {label:<34} heap={current:7.1f} MB  peak={peak:7.1f} MB{suffix}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="stub the provider instead of calling it")
    args = parser.parse_args()

    tracemalloc.start()
    print(f"Query: {QUERY}\n")

    # 1. Import the search path only.
    from backend.smatecondata.api.routes import _make_provider
    from backend.smatecondata.datasets.instant import (
        InstantDatasetService,
        summarise,
    )
    from backend.smatecondata.providers.gateway import ProviderGateway

    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    report("imports (search path)", current / 1048576, peak / 1048576, rss_mb())

    # 2. Heavy libraries must not have been dragged in.
    heavy = [
        lib for lib in ("openpyxl", "pandas", "data_profiling", "matplotlib",
                        "pyarrow", "xlsxwriter", "faiss", "torch",
                        "sentence_transformers")
        if any(m == lib or m.startswith(lib + ".") for m in sys.modules)
    ]
    print(f"  heavy libraries imported          : {heavy or 'none'}")

    # 3. Build the service. No provider should exist yet.
    if args.offline:
        from backend.models import DataPoint, Metadata, NormalizedData

        class Stub:
            async def fetch_data(self, **params):
                return NormalizedData(
                    metadata=Metadata(
                        source="World Bank",
                        indicator="Inflation, consumer prices (annual %)",
                        country="Algeria", frequency="annual", unit="Annual %",
                        seriesId="FP.CPI.TOTL.ZG",
                    ),
                    data=[DataPoint(date=f"{y}-01-01", value=2.0 + y % 5)
                          for y in range(2000, 2025)],
                )

        gateway = ProviderGateway(factory=lambda key: Stub())
    else:
        gateway = ProviderGateway(factory=_make_provider, timeout=20.0)

    service = InstantDatasetService(gateway)
    print(f"  providers before request          : {gateway.instantiated or 'none'}")

    # 4. The request itself.
    started = time.perf_counter()
    result = await service.build(QUERY)
    elapsed = (time.perf_counter() - started) * 1000

    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    report("after instant-dataset", current / 1048576, peak / 1048576, rss_mb())

    print(f"\n  providers instantiated            : {gateway.instantiated or 'none'}")
    print(f"  elapsed                           : {elapsed:.0f} ms")
    if result.resolution:
        chosen = result.resolution[0]
        print(f"  resolved                          : "
              f"{chosen.provider} {chosen.series_id}")
    print(f"  rows                              : "
          f"{result.dataset.row_count if result.dataset else 0}")

    payload = summarise(result, "measurement")
    print(f"  response preview rows             : "
          f"{len(payload['dataset']['preview']) if payload['dataset'] else 0}")

    # 5. Verdict against the Render free tier.
    heap_peak = peak / 1048576
    process = rss_mb()
    print()
    heavy_after = [
        lib for lib in ("openpyxl", "pandas", "data_profiling", "matplotlib")
        if any(m == lib or m.startswith(lib + ".") for m in sys.modules)
    ]
    print(f"  heavy libraries after request     : {heavy_after or 'none'}")

    budget = process or heap_peak
    verdict = "WITHIN" if budget < RENDER_LIMIT_MB * 0.6 else "AT RISK"
    print(f"\n  Render free limit                 : {RENDER_LIMIT_MB} MB")
    print(f"  measured                          : {budget:.0f} MB  =>  {verdict}")

    tracemalloc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
