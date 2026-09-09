"""Catalogue statistics and scale milestones (spec 0.1A).

The spec's rule here is blunt and this module exists to enforce it:

    Never hard-code `500K+`, `800K+`, or `1M+` into marketing/UI text.
    Always calculate the displayed number from the actual production index.

So every number below is computed from the live index. :func:`display_count`
is the only sanctioned way to render a catalogue size, and it will not emit a
milestone badge the index has not actually reached.

Three thresholds, from the spec:

    legacy non-regression floor : >=   331,000 searchable series
    initial expansion target    : >=   500,000
    production stretch target   : >= 1,000,000

**What ships in this repository is smaller than the floor.** The bundled
provider metadata carries roughly 42K series; the 331K figure describes a
fully-synchronised production catalogue built by
``scripts/fetch_all_indicators.py`` against live provider APIs. Reporting the
real number is the point -- see :meth:`CatalogStats.milestone`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .index import METADATA_DIR, PROVIDER_FILES
from .store import CatalogStore, get_catalog_store

# Spec 0.1A thresholds.
LEGACY_FLOOR = 331_000
EXPANSION_TARGET = 500_000
STRETCH_TARGET = 1_000_000


@dataclass
class CatalogStats:
    """A truthful snapshot of the catalogue (spec 0.1A)."""

    searchable_series_count: int = 0
    raw_provider_series_count: int = 0
    deduplicated_concept_count: int = 0
    provider_count: int = 0
    concept_count: int = 0
    country_or_area_count: int = 0
    frequency_breakdown: dict[str, int] = field(default_factory=dict)
    provider_breakdown: dict[str, int] = field(default_factory=dict)
    topic_breakdown: dict[str, int] = field(default_factory=dict)
    last_catalog_sync: str | None = None
    new_series_last_30_days: int = 0
    updated_series_last_30_days: int = 0

    # -- milestones ------------------------------------------------------

    @property
    def meets_legacy_floor(self) -> bool:
        return self.searchable_series_count >= LEGACY_FLOOR

    @property
    def meets_expansion_target(self) -> bool:
        return self.searchable_series_count >= EXPANSION_TARGET

    @property
    def meets_stretch_target(self) -> bool:
        return self.searchable_series_count >= STRETCH_TARGET

    def milestone(self) -> str:
        """Which scale band the catalogue is actually in."""
        if self.meets_stretch_target:
            return "stretch"
        if self.meets_expansion_target:
            return "expansion"
        if self.meets_legacy_floor:
            return "floor"
        return "below_floor"

    def display_count(self) -> str:
        """The catalogue size, rendered for the UI.

        The compact ``1M+`` label is only ever produced once the index really
        holds a million records; below that the exact number is shown.
        """
        count = self.searchable_series_count
        if count >= STRETCH_TARGET:
            return "1M+ economic series"
        return f"{count:,} indexed series"

    def next_milestone(self) -> dict[str, Any] | None:
        """The next threshold and how far away it is, or None at the top."""
        for name, target in (
            ("legacy_floor", LEGACY_FLOOR),
            ("expansion_target", EXPANSION_TARGET),
            ("stretch_target", STRETCH_TARGET),
        ):
            if self.searchable_series_count < target:
                return {
                    "name": name,
                    "target": target,
                    "remaining": target - self.searchable_series_count,
                    "progress_pct": round(
                        100.0 * self.searchable_series_count / target, 2
                    ),
                }
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "searchable_series_count": self.searchable_series_count,
            "raw_provider_series_count": self.raw_provider_series_count,
            "deduplicated_concept_count": self.deduplicated_concept_count,
            "provider_count": self.provider_count,
            "concept_count": self.concept_count,
            "country_or_area_count": self.country_or_area_count,
            "frequency_breakdown": self.frequency_breakdown,
            "provider_breakdown": self.provider_breakdown,
            "topic_breakdown": dict(list(self.topic_breakdown.items())[:25]),
            "last_catalog_sync": self.last_catalog_sync,
            "new_series_last_30_days": self.new_series_last_30_days,
            "updated_series_last_30_days": self.updated_series_last_30_days,
            "display_count": self.display_count(),
            "milestone": self.milestone(),
            "meets_legacy_floor": self.meets_legacy_floor,
            "meets_expansion_target": self.meets_expansion_target,
            "meets_stretch_target": self.meets_stretch_target,
            "next_milestone": self.next_milestone(),
            "thresholds": {
                "legacy_floor": LEGACY_FLOOR,
                "expansion_target": EXPANSION_TARGET,
                "stretch_target": STRETCH_TARGET,
            },
            "note": (
                "Counts are computed from the live index, never hard-coded. "
                "Expand the catalogue with scripts/fetch_all_indicators.py."
            ),
        }


class CatalogStatsService:
    """Computes :class:`CatalogStats` from the real index (spec 0.1A)."""

    def __init__(self, store: CatalogStore | None = None,
                 metadata_dir: Path | None = None):
        self._store = store
        self.metadata_dir = metadata_dir or METADATA_DIR

    @property
    def store(self) -> CatalogStore:
        """Counts come from SQLite aggregates, not from loading the catalogue."""
        if self._store is None:
            self._store = get_catalog_store()
        return self._store

    def _sync_metadata(self) -> tuple[str | None, int, int, int]:
        """``(last sync, raw provider total, new in 30d, updated in 30d)``.

        Provider files carry a ``last_updated`` stamp and a
        ``total_indicators`` figure. The raw total is what the providers claim;
        the indexed count is what we could actually parse -- the spec asks for
        both to be tracked separately.
        """
        latest: datetime | None = None
        raw_total = 0
        recent_new = 0
        recent_updated = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        for stem in PROVIDER_FILES:
            path = self.metadata_dir / f"{stem}.json"
            if not path.exists():
                continue

            header = self._read_header(path)
            if header is None:
                continue

            total = header.get("total_indicators") or 0
            raw_total += int(total)

            stamp = header.get("last_updated")
            if not isinstance(stamp, str):
                continue
            try:
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if latest is None or when > latest:
                latest = when
            if when >= cutoff:
                # A provider file refreshed inside the window contributes its
                # series count to the "updated" tally. Without per-series
                # history this is the honest granularity available.
                recent_updated += int(total)

        return (
            latest.isoformat() if latest else None,
            raw_total,
            recent_new,
            recent_updated,
        )

    @staticmethod
    def _read_header(path: Path, probe_bytes: int = 4096) -> dict[str, Any] | None:
        """Read a provider file's header fields without parsing the whole file.

        `json.loads` on the metadata directory peaked at 272 MB, because
        worldbank.json alone is 62 MB and every byte was decoded to reach two
        scalar fields that sit in the first few hundred bytes. The header is
        read directly instead, so this costs kilobytes regardless of catalogue
        size.
        """
        try:
            with path.open("r", encoding="utf-8") as handle:
                head = handle.read(probe_bytes)
        except OSError:
            return None

        result: dict[str, Any] = {}
        total = re.search(r'"total_indicators"\s*:\s*(\d+)', head)
        if total:
            result["total_indicators"] = int(total.group(1))
        stamp = re.search(r'"last_updated"\s*:\s*"([^"]+)"', head)
        if stamp:
            result["last_updated"] = stamp.group(1)
        return result or None

    def compute(self) -> CatalogStats:
        store = self.store
        providers = store.provider_counts()
        frequencies = store.frequency_counts()
        topics = store.topic_counts()

        last_sync, raw_total, recent_new, recent_updated = self._sync_metadata()

        from ..search.aliases import get_concept_store
        from ..search.geography import COUNTRY_ALIASES

        # The concept catalogue is small and already in memory; counting it
        # costs nothing, unlike scanning every series.
        concepts = get_concept_store().concepts
        deduplicated = len(concepts)

        return CatalogStats(
            searchable_series_count=store.count(),
            raw_provider_series_count=raw_total,
            deduplicated_concept_count=deduplicated,
            provider_count=len(providers),
            concept_count=len(concepts),
            country_or_area_count=len(COUNTRY_ALIASES),
            frequency_breakdown=frequencies,
            provider_breakdown=providers,
            topic_breakdown=topics,
            last_catalog_sync=last_sync,
            new_series_last_30_days=recent_new,
            updated_series_last_30_days=recent_updated,
        )


_service: CatalogStatsService | None = None


def get_catalog_stats_service() -> CatalogStatsService:
    global _service
    if _service is None:
        _service = CatalogStatsService()
    return _service
