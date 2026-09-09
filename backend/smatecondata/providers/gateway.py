"""Bridge from the existing provider connectors to the canonical models.

The connectors in ``backend/providers`` return :class:`NormalizedData`
(a provider-shaped metadata block plus dated points). The dataset layer speaks
:class:`IndicatorMetadata` and :class:`Observation`. This module is the single
place that translates between the two, so neither side has to know about the
other.

Failure handling follows spec 36: a provider that times out, rate-limits or
returns nothing for some geographies yields a structured
:class:`ProviderResult` describing exactly what came back. One failing provider
never aborts the whole request, and a missing country is reported rather than
silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ...models import NormalizedData
from ..core.models import (
    Frequency,
    IndicatorMetadata,
    Observation,
    ProviderResult,
    utcnow,
)
from ..search.geography import COUNTRY_ALIASES, GeographyResolver, get_geography_resolver

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45.0

# Canonical internal provider keys -> the connector attribute on QueryService.
PROVIDER_ATTRS: dict[str, str] = {
    "world_bank": "world_bank_provider",
    "fred": "fred_provider",
    "imf": "imf_provider",
    "eurostat": "eurostat_provider",
    "oecd": "oecd_provider",
    "bis": "bis_provider",
    "comtrade": "comtrade_provider",
    "statscan": "statscan_provider",
    "exchangerate": "exchangerate_provider",
    "coingecko": "coingecko_provider",
    "chinamacro": "chinamacro_provider",
}

# Provider names as they appear in NormalizedData.metadata.source.
_SOURCE_TO_KEY = {
    "world bank": "world_bank",
    "worldbank": "world_bank",
    "fred": "fred",
    "imf": "imf",
    "eurostat": "eurostat",
    "oecd": "oecd",
    "bis": "bis",
    "un comtrade": "comtrade",
    "comtrade": "comtrade",
    "statistics canada": "statscan",
    "statscan": "statscan",
    "exchangerate-api": "exchangerate",
    "coingecko": "coingecko",
    "chinamacro": "chinamacro",
}

# Period shapes the connectors emit, mapped to a canonical label.
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR_QUARTER = re.compile(r"^(\d{4})[-]?Q([1-4])$", re.IGNORECASE)
_YEAR_ONLY = re.compile(r"^(\d{4})$")


def normalise_provider_key(source: str | None) -> str:
    if not source:
        return "unknown"
    return _SOURCE_TO_KEY.get(source.strip().lower(),
                              source.strip().lower().replace(" ", "_"))


def canonical_period(raw: str, frequency: Frequency) -> str:
    """Collapse a provider date onto the label for its declared frequency.

    An annual series that reports ``2020-01-01`` becomes ``2020``; a quarterly
    one becomes ``2020Q1``. Without this the wide pivot would key every country
    on a slightly different string and produce a diagonal table.
    """
    raw = (raw or "").strip()

    if m := _ISO_DATE.match(raw):
        year, month, _ = m.groups()
        if frequency is Frequency.ANNUAL:
            return year
        if frequency is Frequency.QUARTERLY:
            return f"{year}Q{(int(month) - 1) // 3 + 1}"
        if frequency is Frequency.MONTHLY:
            return f"{year}-{month}"
        return raw

    if m := _YEAR_MONTH.match(raw):
        year, month = m.groups()
        if frequency is Frequency.ANNUAL:
            return year
        if frequency is Frequency.QUARTERLY:
            return f"{year}Q{(int(month) - 1) // 3 + 1}"
        return raw

    if m := _YEAR_QUARTER.match(raw):
        year, quarter = m.groups()
        return year if frequency is Frequency.ANNUAL else f"{year}Q{quarter}"

    if _YEAR_ONLY.match(raw):
        return raw

    return raw


def _first_attr(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def adapt(normalized: NormalizedData,
          *,
          resolver: GeographyResolver | None = None,
          requested_geography: str | None = None,
          ) -> tuple[IndicatorMetadata, list[Observation]]:
    """Convert one :class:`NormalizedData` into canonical models."""
    resolver = resolver or get_geography_resolver()
    meta = normalized.metadata

    provider = normalise_provider_key(meta.source)
    frequency = Frequency.coerce(meta.frequency)
    series_id = _first_attr(meta, "seriesId") or meta.indicator

    geography_name = meta.country or requested_geography or "Unknown"
    iso3 = (
        resolver.iso3_for(geography_name)
        or (requested_geography.upper()
            if requested_geography and requested_geography.upper() in COUNTRY_ALIASES
            else None)
    )
    display = (
        resolver.display_name(iso3) if iso3 else geography_name
    )

    metadata = IndicatorMetadata(
        provider=provider,
        series_id=str(series_id),
        title=meta.indicator or str(series_id),
        description=_first_attr(meta, "notes", "description"),
        unit=meta.unit or None,
        frequency=frequency,
        source_name=meta.source,
        source_reference=_first_attr(meta, "sourceUrl", "apiUrl"),
        last_updated=meta.lastUpdated or None,
        seasonal_adjustment=_first_attr(meta, "seasonalAdjustment"),
        geographies=[iso3] if iso3 else [],
    )

    retrieved_at = utcnow()
    observations = [
        Observation(
            provider=provider,
            series_id=str(series_id),
            geography=display,
            iso3=iso3,
            period=canonical_period(point.date, frequency),
            value=point.value,
            unit=metadata.unit,
            frequency=frequency,
            retrieved_at=retrieved_at,
        )
        for point in normalized.data
    ]

    # Coverage comes from what actually arrived, not from a catalogue claim.
    periods = [o.period for o in observations if o.value is not None]
    if periods:
        metadata.coverage_start = min(periods)
        metadata.coverage_end = max(periods)

    return metadata, observations


@dataclass
class FetchOutcome:
    """Everything one provider call produced, successes and failures alike."""

    metadata: IndicatorMetadata | None = None
    observations: list[Observation] = field(default_factory=list)
    result: ProviderResult | None = None

    @property
    def ok(self) -> bool:
        return self.metadata is not None and bool(self.observations)


class ProviderGateway:
    """Fetch canonical observations from the existing connectors.

    Construct with a mapping of provider key -> connector instance. In the
    running application that mapping comes from ``QueryService``; in tests a
    stub connector can be supplied directly.
    """

    def __init__(self, connectors: dict[str, Any],
                 *, timeout: float = DEFAULT_TIMEOUT):
        self.connectors = connectors
        self.timeout = timeout
        self.resolver = get_geography_resolver()

    @classmethod
    def from_query_service(cls, query_service: Any, **kwargs) -> "ProviderGateway":
        connectors = {}
        for key, attribute in PROVIDER_ATTRS.items():
            connector = getattr(query_service, attribute, None)
            if connector is not None:
                connectors[key] = connector
        return cls(connectors, **kwargs)

    @property
    def available(self) -> list[str]:
        return sorted(self.connectors)

    async def fetch_series(
        self,
        provider: str,
        indicator: str,
        geographies: Sequence[str],
        *,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> FetchOutcome:
        """Fetch one indicator for one or more geographies.

        Returns a :class:`FetchOutcome` describing partial success rather than
        raising, so a caller assembling many series can keep going.
        """
        connector = self.connectors.get(provider)
        if connector is None:
            return FetchOutcome(result=ProviderResult(
                provider=provider, status="failed",
                requested_geographies=len(geographies),
                message=f"Provider '{provider}' is not configured.",
            ))

        params: dict[str, Any] = {"indicator": indicator}
        if len(geographies) == 1:
            params["country"] = geographies[0]
        else:
            params["countries"] = list(geographies)
        if start_year:
            params["start_date"] = str(start_year)
        if end_year:
            params["end_date"] = str(end_year)

        try:
            payload = await asyncio.wait_for(
                connector.fetch_data(**params), timeout=self.timeout)
        except asyncio.TimeoutError:
            return FetchOutcome(result=ProviderResult(
                provider=provider, status="failed",
                requested_geographies=len(geographies),
                failed_geographies=list(geographies),
                message=f"Provider '{provider}' timed out after {self.timeout:g}s.",
            ))
        except Exception as exc:  # connector errors are data, not crashes
            logger.warning("provider %s failed for %s: %s", provider, indicator, exc)
            return FetchOutcome(result=ProviderResult(
                provider=provider, status="failed",
                requested_geographies=len(geographies),
                failed_geographies=list(geographies),
                message=f"{type(exc).__name__}: {exc}",
            ))

        return self._collect(provider, payload, geographies)

    def _collect(self, provider: str, payload: Any,
                 geographies: Sequence[str]) -> FetchOutcome:
        blocks: list[NormalizedData] = (
            list(payload) if isinstance(payload, (list, tuple)) else [payload]
        )
        blocks = [b for b in blocks if b is not None]

        metadata: IndicatorMetadata | None = None
        observations: list[Observation] = []
        seen: set[str] = set()

        for block in blocks:
            try:
                block_meta, block_obs = adapt(
                    block, resolver=self.resolver,
                    requested_geography=geographies[0] if len(geographies) == 1 else None)
            except Exception as exc:
                logger.warning("could not adapt %s response: %s", provider, exc)
                continue
            if metadata is None:
                metadata = block_meta
            observations.extend(block_obs)
            for observation in block_obs:
                if observation.value is not None:
                    seen.add((observation.iso3 or observation.geography).upper())

        requested = {g.upper() for g in geographies}
        missing = sorted(requested - seen) if requested else []

        if metadata is None or not observations:
            return FetchOutcome(result=ProviderResult(
                provider=provider, status="failed",
                requested_geographies=len(geographies),
                failed_geographies=list(geographies),
                message=f"Provider '{provider}' returned no usable observations.",
            ))

        # Widen coverage to every geography that actually returned data.
        metadata.geographies = sorted(seen)

        status = "partial_success" if missing else "success"
        message = (
            f"Provider returned no observations for {len(missing)} "
            f"requested geograph{'y' if len(missing) == 1 else 'ies'}."
            if missing else None
        )
        return FetchOutcome(
            metadata=metadata,
            observations=observations,
            result=ProviderResult(
                provider=provider,
                status=status,
                requested_geographies=len(geographies),
                successful_geographies=len(seen & requested) if requested else len(seen),
                failed_geographies=missing,
                message=message,
            ),
        )

    async def fetch_many(
        self,
        requests: Iterable[tuple[str, str, str | None]],
        geographies: Sequence[str],
        *,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[tuple[str | None, FetchOutcome]]:
        """Fetch several ``(provider, indicator, concept)`` triples in parallel.

        Ordering of the returned list matches the input, so a caller can pair
        each outcome with the concept it was asked for.
        """
        triples = list(requests)
        tasks = [
            self.fetch_series(provider, indicator, geographies,
                              start_year=start_year, end_year=end_year)
            for provider, indicator, _ in triples
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        paired: list[tuple[str | None, FetchOutcome]] = []
        for (provider, _indicator, concept), outcome in zip(triples, outcomes):
            if isinstance(outcome, BaseException):
                outcome = FetchOutcome(result=ProviderResult(
                    provider=provider, status="failed",
                    requested_geographies=len(geographies),
                    message=f"{type(outcome).__name__}: {outcome}",
                ))
            paired.append((concept, outcome))
        return paired
