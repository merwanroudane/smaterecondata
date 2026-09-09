"""Deterministic natural-language request parser (spec 0A.1, 0P, 6.1).

Turns ``Inflation in Algeria from 2000 to 2025`` -- or its Arabic or French
equivalent -- into a typed :class:`DatasetSpec` with **no LLM involved**. The
spec makes this non-negotiable: core discovery must work without any API key,
and an LLM may only ever be an optional enhancement layer on top.

The parser never invents data. It resolves *what was asked for*; choosing the
actual provider series is the ranking layer's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.models import (
    DatasetSpec,
    Frequency,
    IndicatorRequest,
    Language,
    OutputShape,
)
from .aliases import Concept, get_concept_store
from .geography import extract_years, get_geography_resolver
from .normalize import detect_language, normalize

# Frequency wording in all three languages.
_FREQUENCY_HINTS: list[tuple[Frequency, tuple[str, ...]]] = [
    (Frequency.ANNUAL, ("annual", "annually", "yearly", "per year", "annuel",
                        "annuelle", "par an", "سنوي", "سنويه", "سنويا")),
    (Frequency.QUARTERLY, ("quarterly", "quarter", "trimestriel", "trimestrielle",
                           "ربع سنوي", "فصلي", "ربعي")),
    (Frequency.MONTHLY, ("monthly", "month", "mensuel", "mensuelle", "شهري",
                         "شهريا")),
    (Frequency.WEEKLY, ("weekly", "hebdomadaire", "اسبوعي")),
    (Frequency.DAILY, ("daily", "quotidien", "journalier", "يومي")),
]

# Provider names users actually type.
_PROVIDER_HINTS: dict[str, tuple[str, ...]] = {
    "world_bank": ("world bank", "worldbank", "banque mondiale", "wb",
                   "البنك الدولي"),
    "imf": ("imf", "international monetary fund", "fmi",
            "صندوق النقد الدولي", "صندوق النقد"),
    "fred": ("fred", "st louis fed", "federal reserve", "الاحتياطي الفيدرالي"),
    "oecd": ("oecd", "ocde", "منظمة التعاون الاقتصادي"),
    "eurostat": ("eurostat", "يوروستات"),
    "bis": ("bis", "bank for international settlements",
            "بنك التسويات الدولية"),
}

_EXPORT_HINTS: dict[str, tuple[str, ...]] = {
    "xlsx": ("excel", "xlsx", "workbook", "classeur", "اكسل", "إكسل"),
    "csv": ("csv",),
    "parquet": ("parquet",),
    "json": ("json",),
    "html": ("html report", "html", "report", "rapport", "تقرير"),
    "dta": ("stata", "dta"),
}

_WIDE_HINTS = ("wide", "large", "wide format", "format large", "عريض")
_LONG_HINTS = ("long", "long format", "format long", "panel", "طويل")

# "latest", "last N years", "since YYYY" style windows.
_LAST_N_YEARS = re.compile(
    r"\b(?:last|past|dernieres?|derniers?|آخر|اخر)\s+(\d{1,3})\s*"
    r"(?:years?|ans|annees?|سنوات|سنه|سنة)\b"
)
_SINCE = re.compile(r"\b(?:since|from|depuis|منذ|من)\s+(1[89]\d{2}|20\d{2})\b")


@dataclass
class ParsedQuery:
    """The parse result plus everything needed to explain it to the user."""

    spec: DatasetSpec
    language: Language
    concepts: list[Concept] = field(default_factory=list)
    unmatched_terms: list[str] = field(default_factory=list)
    ambiguous_concepts: list[Concept] = field(default_factory=list)

    def understanding(self) -> dict[str, object]:
        """The transparent "What I understood" panel of spec 0B mode 9."""
        resolver = get_geography_resolver()
        return {
            "countries": [
                resolver.display_name(g, self.language) for g in self.spec.geographies
            ],
            "iso3": list(self.spec.geographies),
            "indicators": [c.display(self.language) for c in self.concepts],
            "concept_keys": [c.key for c in self.concepts],
            "period": self.spec.period_label(),
            "frequency": self.spec.frequency.value,
            "preferred_sources": self.spec.preferred_sources or ["automatic"],
            "output": self.spec.export_formats,
            "language": self.language.value,
            "needs_clarification": [c.key for c in self.ambiguous_concepts],
        }


class QueryParser:
    """Rule-based tri-lingual request parser."""

    def __init__(self) -> None:
        self.concepts = get_concept_store()
        self.geography = get_geography_resolver()

    def parse(self, query: str,
              language: Language | None = None,
              current_year: int | None = None) -> ParsedQuery:
        language = language or detect_language(query)
        normalised = normalize(query, language)

        geo_matches = self.geography.find_in_query(query, language)
        concepts = self.concepts.find_in_query(query, language)
        start, end = self._resolve_period(query, normalised, current_year)

        spec = DatasetSpec(
            geographies=[m.iso3 for m in geo_matches],
            indicators=[
                IndicatorRequest(concept=c.key, alias=c.key)
                for c in concepts
            ],
            start_year=start,
            end_year=end,
            frequency=self._resolve_frequency(normalised),
            preferred_sources=self._resolve_providers(normalised),
            output_shape=self._resolve_shape(normalised),
            export_formats=self._resolve_exports(normalised),
            original_query=query,
            query_language=language,
        )

        return ParsedQuery(
            spec=spec,
            language=language,
            concepts=concepts,
            ambiguous_concepts=[c for c in concepts if c.ambiguous],
        )

    # -- individual fields ------------------------------------------------

    def _resolve_period(self, query: str, normalised: str,
                        current_year: int | None) -> tuple[int | None, int | None]:
        start, end = extract_years(query)
        if start is not None:
            # "since 2000" with no closing year means "through latest".
            if end is None and _SINCE.search(normalised):
                return start, None
            return start, end

        window = _LAST_N_YEARS.search(normalised)
        if window and current_year:
            n = int(window.group(1))
            return current_year - n + 1, current_year
        return None, None

    @staticmethod
    def _resolve_frequency(normalised: str) -> Frequency:
        """Longest matching hint wins, regardless of declaration order.

        Order alone is not enough: Arabic ``ربع سنوي`` (quarterly) *contains*
        ``سنوي`` (annual), and English ``quarterly`` sits inside no other hint
        but ``per year`` overlaps ``year``. Scoring by hint length picks the
        more specific reading in every such case.
        """
        padded = f" {normalised} "
        best: tuple[int, Frequency] | None = None
        for frequency, hints in _FREQUENCY_HINTS:
            for hint in hints:
                if f" {hint} " in padded:
                    score = len(hint)
                    if best is None or score > best[0]:
                        best = (score, frequency)
        if best:
            return best[1]
        # Annual is the sane default for cross-country macro work, and the
        # spec asks for a sensible default rather than a blocking question.
        return Frequency.ANNUAL

    @staticmethod
    def _resolve_providers(normalised: str) -> list[str]:
        padded = f" {normalised} "
        found = [
            provider
            for provider, hints in _PROVIDER_HINTS.items()
            if any(f" {h} " in padded for h in hints)
        ]
        return found

    @staticmethod
    def _resolve_shape(normalised: str) -> OutputShape:
        padded = f" {normalised} "
        if any(f" {h} " in padded for h in _LONG_HINTS):
            return OutputShape.LONG
        if any(f" {h} " in padded for h in _WIDE_HINTS):
            return OutputShape.WIDE
        return OutputShape.WIDE

    @staticmethod
    def _resolve_exports(normalised: str) -> list[str]:
        padded = f" {normalised} "
        found = [
            fmt
            for fmt, hints in _EXPORT_HINTS.items()
            if any(f" {h} " in padded for h in hints)
        ]
        return found or ["xlsx"]


_parser: QueryParser | None = None


def get_query_parser() -> QueryParser:
    global _parser
    if _parser is None:
        _parser = QueryParser()
    return _parser


def parse_query(query: str, **kwargs) -> ParsedQuery:
    return get_query_parser().parse(query, **kwargs)
