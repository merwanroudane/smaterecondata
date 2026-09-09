#!/usr/bin/env python3
"""Expand the catalogue from KEY-FREE providers only (spec 0.1A).

``scripts/fetch_all_indicators.py`` covers the providers the upstream project
already supported, but its largest contributor (FRED) needs an API key. This
script adds the open, no-registration sources the spec lists as expansion
candidates, so the catalogue can grow without anyone signing up for anything:

    ILOSTAT      SDMX dataflows      https://sdmx.ilo.org
    ECB          SDMX dataflows      https://data-api.ecb.europa.eu
    UN SDG       indicator series    https://unstats.un.org/SDGAPI

Output is written in the same shape as ``backend/data/metadata/*.json`` so the
catalogue index picks it up with no code change.

Honest about scale: these sources add low thousands of series, not hundreds of
thousands. Reaching the spec's 331,000 floor requires FRED, which needs a free
key. This script never invents an entry to close that gap.

    python scripts/fetch_open_catalog.py            # fetch all
    python scripts/fetch_open_catalog.py --provider ilostat
    python scripts/fetch_open_catalog.py --dry-run  # counts only, no writes
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
METADATA_DIR = REPO / "backend" / "data" / "metadata"

USER_AGENT = "SmatEconData/1.0 (+https://github.com/merwanroudane/smaterecondata)"
TIMEOUT = 120

# SDMX 2.1 puts Dataflow under assorted namespace prefixes.
_DATAFLOW = re.compile(
    rb"<(?:s|str|structure):Dataflow\b[^>]*?id=\"([^\"]+)\"[^>]*?>(.*?)"
    rb"</(?:s|str|structure):Dataflow>",
    re.S,
)
_NAME = re.compile(rb"<(?:c|com|common):Name[^>]*>(.*?)</(?:c|com|common):Name>", re.S)
_DESC = re.compile(
    rb"<(?:c|com|common):Description[^>]*>(.*?)</(?:c|com|common):Description>", re.S
)
_TAG = re.compile(rb"<[^>]+>")


def _text(raw: bytes) -> str:
    """Strip tags and decode one SDMX text node."""
    cleaned = _TAG.sub(b"", raw).strip()
    text = cleaned.decode("utf-8", "replace")
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(entity, char)
    return " ".join(text.split())


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS, using certifi's CA bundle when it is installed.

    Python on Windows does not read the OS trust store, so a perfectly valid
    certificate (the ECB's, for one) fails with "unable to get local issuer
    certificate". certifi supplies the missing roots. Verification is never
    disabled: a catalogue is provenance, and provenance fetched over an
    unverified channel is worthless.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str, *, timeout: int = TIMEOUT, retries: int = 2) -> bytes:
    """GET with certificate verification left ON, retrying transient failures."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = _ssl_context()

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            # A read timeout or reset is worth one more try; a 4xx is not.
            if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500:
                raise
            last = exc
            if attempt < retries:
                print(f"    retry {attempt + 1}/{retries} after "
                      f"{type(exc).__name__}", flush=True)
    raise last if last else RuntimeError("fetch failed")


def _sdmx_dataflows(raw: bytes, provider: str, source: str,
                    reference: str) -> list[dict[str, Any]]:
    indicators: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in _DATAFLOW.finditer(raw):
        code = match.group(1).decode("utf-8", "replace").strip()
        if not code or code in seen:
            continue
        body = match.group(2)

        name_match = _NAME.search(body)
        name = _text(name_match.group(1)) if name_match else code
        if not name:
            name = code

        desc_match = _DESC.search(body)
        description = _text(desc_match.group(1)) if desc_match else ""

        seen.add(code)
        indicators.append({
            "id": f"{provider.upper()}_{code}",
            "code": code,
            "name": name,
            "description": description,
            "category": "Other",
            "type": "sdmx_dataflow",
            "source": source,
            "source_url": reference,
            "aliases": [code, name.upper()][:2],
        })
    return indicators


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def fetch_ilostat() -> list[dict[str, Any]]:
    """International Labour Organization — SDMX dataflows, no key."""
    raw = fetch("https://sdmx.ilo.org/rest/dataflow")
    return _sdmx_dataflows(raw, "ilostat", "ILOSTAT", "https://ilostat.ilo.org/data/")


def fetch_ecb() -> list[dict[str, Any]]:
    """European Central Bank Data Portal — SDMX dataflows, no key."""
    raw = fetch("https://data-api.ecb.europa.eu/service/dataflow")
    return _sdmx_dataflows(raw, "ecb", "ECB Data Portal", "https://data.ecb.europa.eu/")


def fetch_unsd() -> list[dict[str, Any]]:
    """UN Statistics Division — SDG indicator series, no key."""
    raw = fetch("https://unstats.un.org/SDGAPI/v1/sdg/Series/List")
    payload = json.loads(raw)

    indicators: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in payload if isinstance(payload, list) else []:
        code = str(entry.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        goal = entry.get("goal")
        goals = ", ".join(str(g) for g in goal) if isinstance(goal, list) else str(goal or "")
        indicators.append({
            "id": f"UNSD_{code}",
            "code": code,
            "name": str(entry.get("description") or code),
            "description": f"UN SDG series {code}"
                           + (f" (Goal {goals})" if goals else ""),
            "category": "Development",
            "type": "sdg_series",
            "source": "UN Statistics Division",
            "source_url": "https://unstats.un.org/sdgs/dataportal",
            "aliases": [code],
        })
    return indicators


PROVIDERS: dict[str, tuple[str, Callable[[], list[dict[str, Any]]]]] = {
    "ilostat": ("ILOSTAT", fetch_ilostat),
    "ecb": ("ECB", fetch_ecb),
    "unsd": ("UNSD", fetch_unsd),
}


def write_metadata(stem: str, provider_label: str,
                   indicators: list[dict[str, Any]]) -> Path:
    """Write in the same shape the catalogue index already reads."""
    path = METADATA_DIR / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": provider_label,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_indicators": len(indicators),
        "fetched_by": "scripts/fetch_open_catalog.py",
        "requires_api_key": False,
        "indicators": indicators,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8", newline="\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", "-p", choices=sorted(PROVIDERS),
                        help="fetch only this provider")
    parser.add_argument("--dry-run", action="store_true",
                        help="report counts without writing")
    args = parser.parse_args()

    selected = [args.provider] if args.provider else sorted(PROVIDERS)
    total = 0
    failures: list[str] = []

    for stem in selected:
        label, fetcher = PROVIDERS[stem]
        print(f"fetching {label} ...", flush=True)
        try:
            indicators = fetcher()
        except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError,
                json.JSONDecodeError, TimeoutError) as exc:
            # One unreachable provider must not abort the rest.
            print(f"  FAILED {type(exc).__name__}: {exc}")
            failures.append(label)
            continue

        if not indicators:
            print("  no indicators parsed — leaving existing metadata untouched")
            failures.append(label)
            continue

        total += len(indicators)
        if args.dry_run:
            print(f"  would write {len(indicators):,} indicators")
        else:
            path = write_metadata(stem, label, indicators)
            print(f"  wrote {len(indicators):,} indicators -> "
                  f"{path.relative_to(REPO).as_posix()}")

    print(f"\ntotal from key-free providers: {total:,}")
    if failures:
        print(f"unavailable this run: {', '.join(failures)}")
    print("\nFRED is the largest single source (800k+ series) and needs a free "
          "key; run scripts/fetch_all_indicators.py with FRED_API_KEY set to "
          "include it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
