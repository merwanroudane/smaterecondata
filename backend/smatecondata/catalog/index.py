"""Searchable catalogue over the bundled provider metadata (spec 0.1A, 6.2, 26).

Loads every provider's metadata file into canonical
:class:`~smatecondata.core.models.IndicatorMetadata` and builds an in-memory
inverted index, so the trilingual discovery layer searches the *real*
catalogue rather than a curated concept list.

Ranking is hybrid, in the order the spec sets out (section 6.2):

1. exact provider-code match  -- an advanced user typing ``NY.GDP.PCAP.CD``
   must get that series first, ahead of anything textual;
2. concept-alias match -- an Arabic or French query resolves to a concept key,
   whose English aliases then match the provider's English titles;
3. lexical BM25 over title, description, category and provider aliases.

The catalogue is metadata only. Nothing here fetches observations, and no
count is ever invented -- see :mod:`smatecondata.catalog.stats`.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..core.models import Frequency, IndicatorMetadata, Language, SearchHit
from ..search.aliases import get_concept_store
from ..search.normalize import normalize, normalize_all

logger = logging.getLogger(__name__)

METADATA_DIR = Path(__file__).resolve().parents[2] / "data" / "metadata"

# File stem -> canonical internal provider key.
PROVIDER_FILES: dict[str, str] = {
    "worldbank": "world_bank",
    "fred": "fred",
    "imf": "imf",
    "eurostat": "eurostat",
    "oecd": "oecd",
    "bis": "bis",
    "statscan": "statscan",
    # Key-free expansion providers (scripts/fetch_open_catalog.py).
    "ilostat": "ilostat",
    "ecb": "ecb",
    "unsd": "unsd",
}

# Provider landing pages, used to build a source reference when the metadata
# does not carry one.
SOURCE_URLS: dict[str, str] = {
    "world_bank": "https://data.worldbank.org/indicator/{code}",
    "fred": "https://fred.stlouisfed.org/series/{code}",
    "eurostat": "https://ec.europa.eu/eurostat/databrowser/view/{code}",
    "oecd": "https://data-explorer.oecd.org/",
    "imf": "https://data.imf.org/",
    "bis": "https://data.bis.org/",
    "statscan": "https://www150.statcan.gc.ca/",
    "ilostat": "https://ilostat.ilo.org/data/",
    "ecb": "https://data.ecb.europa.eu/",
    "unsd": "https://unstats.un.org/sdgs/dataportal",
}

_CODE_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{2,}$")
_TOKEN = re.compile(r"[a-z0-9]+")

# Tokens so common in this catalogue that they carry no discriminating signal.
_STOP_TOKENS = {
    "the", "of", "and", "for", "in", "to", "a", "by", "on", "at", "with",
    "total", "all", "data", "index", "annual", "value",
}


# Field weights. A term in the official title is far more meaningful than the
# same term buried in a long description -- without this, an entry whose
# description merely mentions "GDP per capita" outranks the series actually
# called "GDP per capita (current US$)".
TITLE_WEIGHT = 8.0
ALIAS_WEIGHT = 3.0
TOPIC_WEIGHT = 2.0
BODY_WEIGHT = 1.0


@dataclass
class CatalogEntry:
    """One indexed provider series."""

    metadata: IndicatorMetadata
    tokens: tuple[str, ...]
    #: token -> accumulated field weight, used for ranking
    weights: dict[str, float] = field(default_factory=dict)
    normalised_title: str = ""
    concept_keys: frozenset[str] = frozenset()

    @property
    def key(self) -> str:
        return self.metadata.key


def _as_frequency(raw: object) -> Frequency:
    return Frequency.coerce(raw) if raw else Frequency.UNKNOWN


def _entry_from_raw(raw: dict, provider: str) -> IndicatorMetadata | None:
    """Map one provider metadata record onto the canonical model."""
    code = str(raw.get("code") or raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not code or not name:
        return None

    aliases = raw.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]

    reference = raw.get("source_url") or raw.get("url")
    if not reference:
        template = SOURCE_URLS.get(provider, "")
        reference = template.format(code=code) if "{code}" in template else template

    return IndicatorMetadata(
        provider=provider,
        series_id=code,
        title=name,
        description=str(raw.get("description") or "") or None,
        unit=raw.get("unit") or None,
        frequency=_as_frequency(raw.get("frequency")),
        topic=raw.get("category") or None,
        source_name=raw.get("source") or provider,
        source_reference=reference or None,
        last_updated=raw.get("last_updated") or None,
        aliases={"provider": [str(a) for a in aliases][:12]} if aliases else {},
    )


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN.findall(text.lower())
        if len(token) > 1 and token not in _STOP_TOKENS
    ]


class CatalogIndex:
    """In-memory inverted index over the bundled provider catalogue."""

    def __init__(self, entries: Sequence[CatalogEntry]):
        self.entries: list[CatalogEntry] = list(entries)

        # token -> [(entry position, field-weighted term frequency)]
        self._postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        # normalised provider code -> entry positions (exact-code lookup)
        self._by_code: dict[str, list[int]] = defaultdict(list)
        # concept key -> entry positions
        self._by_concept: dict[str, list[int]] = defaultdict(list)

        total_length = 0
        for position, entry in enumerate(self.entries):
            for token, weight in entry.weights.items():
                self._postings[token].append((position, weight))
            total_length += len(entry.tokens)

            self._by_code[entry.metadata.series_id.lower()].append(position)
            for concept in entry.concept_keys:
                self._by_concept[concept].append(position)

        self.average_length = total_length / len(self.entries) if self.entries else 0.0

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, directory: Path | None = None) -> "CatalogIndex":
        directory = directory or METADATA_DIR
        store = get_concept_store()

        # Concept alias -> concept key, for tagging entries at index time.
        alias_to_concept: dict[str, str] = {}
        for key, concept in store.concepts.items():
            for alias in concept.all_aliases():
                for form in normalize_all(alias):
                    if form and form.isascii():
                        alias_to_concept.setdefault(form, key)

        entries: list[CatalogEntry] = []
        for stem, provider in PROVIDER_FILES.items():
            path = directory / f"{stem}.json"
            if not path.exists():
                logger.warning("catalogue file missing: %s", path)
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("could not read %s: %s", path, exc)
                continue

            for raw in payload.get("indicators") or []:
                if not isinstance(raw, dict):
                    continue
                metadata = _entry_from_raw(raw, provider)
                if metadata is None:
                    continue

                # Accumulate per-field weights rather than one flat bag, so a
                # title hit outweighs a description mention.
                weights: dict[str, float] = defaultdict(float)
                for token in _tokenize(metadata.title):
                    weights[token] += TITLE_WEIGHT
                for token in _tokenize(" ".join(metadata.aliases.get("provider", []))):
                    weights[token] += ALIAS_WEIGHT
                for token in _tokenize(metadata.topic or ""):
                    weights[token] += TOPIC_WEIGHT
                # Descriptions are long and repetitive; cap their contribution
                # so length alone cannot win a ranking.
                for token in _tokenize(metadata.description or "")[:120]:
                    weights[token] += BODY_WEIGHT

                tokens = tuple(weights)

                # Tag with any concept whose alias appears as a phrase in the
                # title, so a trilingual query can reach an English record.
                title_norm = normalize(metadata.title)
                padded = f" {title_norm} "
                concepts = frozenset(
                    key
                    for alias, key in alias_to_concept.items()
                    if f" {alias} " in padded
                )

                entries.append(
                    CatalogEntry(
                        metadata=metadata,
                        tokens=tokens,
                        weights=dict(weights),
                        normalised_title=title_norm,
                        concept_keys=concepts,
                    )
                )

        logger.info("catalogue indexed: %d series from %d providers",
                    len(entries), len(PROVIDER_FILES))
        return cls(entries)

    # -- search -----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        language: Language | None = None,
        providers: Sequence[str] | None = None,
        topic: str | None = None,
        limit: int = 20,
    ) -> list[SearchHit]:
        """Hybrid search over the catalogue."""
        if not query.strip():
            return []

        allowed = {p.lower() for p in providers} if providers else None
        scores: dict[int, float] = defaultdict(float)
        matched: dict[int, set[str]] = defaultdict(set)

        # 1. Exact provider code — decisive, so an advanced user gets their
        #    series first rather than a text match that merely mentions it.
        raw = query.strip()
        if _CODE_LIKE.match(raw):
            for position in self._by_code.get(raw.lower(), []):
                scores[position] += 1000.0
                matched[position].add("code")

        # 2. Concept aliases: this is what carries Arabic and French through.
        concepts = get_concept_store().find_in_query(query, language)
        for rank, concept in enumerate(concepts[:4]):
            weight = 40.0 / (rank + 1)
            for position in self._by_concept.get(concept.key, []):
                scores[position] += weight
                matched[position].add(f"concept:{concept.key}")

        # 3. Field-weighted BM25 over the normalised query.
        normalised_query = normalize(query, language)
        query_tokens = set(_tokenize(normalised_query))

        # The catalogue is written in English. An Arabic or French query has no
        # tokens in common with it, so without this the whole lexical and
        # title-coverage stage is dead and those languages fall back to bare
        # concept membership. Substituting the resolved concept's English
        # aliases gives them the same ranking quality as an English query.
        if concepts and not (query_tokens & self._postings.keys()):
            # Only the CANONICAL English term per concept. Injecting every
            # alias turns the query into a loose bag -- "taux de chomage" would
            # pull in "rate" and "work" from `jobless rate` and surface tax
            # series ahead of unemployment ones.
            english_terms: list[str] = []
            for concept in concepts[:2]:
                aliases = concept.aliases.get("en") or ()
                english_terms.append(aliases[0] if aliases else concept.label)
            translated = " ".join(english_terms)
            if translated:
                normalised_query = normalize(translated, Language.EN)
                query_tokens = set(_tokenize(normalised_query))
                matched_via_concept = True
            else:
                matched_via_concept = False
        else:
            matched_via_concept = False
        for token in query_tokens:
            postings = self._postings.get(token)
            if not postings:
                continue
            idf = math.log(
                1 + (len(self.entries) - len(postings) + 0.5) / (len(postings) + 0.5)
            )
            for position, weighted_frequency in postings:
                length = len(self.entries[position].tokens) or 1
                # BM25 with the conventional k1=1.5, b=0.75, over the
                # field-weighted frequency.
                denominator = weighted_frequency + 1.5 * (
                    1 - 0.75 + 0.75 * length / (self.average_length or 1)
                )
                scores[position] += idf * (weighted_frequency * 2.5) / denominator
                matched[position].add(token)

        # 4. Title-coverage bonus. BM25 rewards rare terms; it does not reward
        #    a title that actually *is* the thing asked for. A series titled
        #    "GDP per capita (current US$)" must beat one that merely mentions
        #    the phrase in its description.
        if query_tokens:
            for position in list(scores):
                entry = self.entries[position]
                title_tokens = set(_tokenize(entry.normalised_title))
                if not title_tokens:
                    continue
                covered = len(query_tokens & title_tokens) / len(query_tokens)
                if covered:
                    scores[position] += 60.0 * covered * covered
                    # Whole normalised phrase present: a near-exact title.
                    if normalised_query and normalised_query in entry.normalised_title:
                        scores[position] += 90.0
                        matched[position].add("title-phrase")
                        # ...and the title *opens* with it, rather than
                        # mentioning it in a parenthetical qualifier such as
                        # "Deposit insurance coverage (% of GDP per capita)".
                        if entry.normalised_title.startswith(normalised_query):
                            scores[position] += 70.0
                            matched[position].add("title-prefix")
                    # Shorter titles are more specific for the same coverage.
                    scores[position] += 12.0 / (1 + len(title_tokens))
                    if matched_via_concept:
                        matched[position].add("translated")

        hits: list[SearchHit] = []
        for position, score in scores.items():
            entry = self.entries[position]
            if allowed and entry.metadata.provider.lower() not in allowed:
                continue
            if topic and (entry.metadata.topic or "").lower() != topic.lower():
                continue
            hits.append(
                SearchHit(
                    metadata=entry.metadata,
                    score=round(score, 4),
                    matched_on=sorted(matched[position])[:6],
                )
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def get(self, provider: str, series_id: str) -> IndicatorMetadata | None:
        for position in self._by_code.get(series_id.lower(), []):
            entry = self.entries[position]
            if entry.metadata.provider == provider:
                return entry.metadata
        return None

    # -- aggregates -------------------------------------------------------

    def providers(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            counts[entry.metadata.provider] += 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def topics(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            counts[entry.metadata.topic or "Uncategorised"] += 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def frequencies(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            counts[entry.metadata.frequency.value] += 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def concept_coverage(self) -> dict[str, int]:
        return {key: len(positions) for key, positions in
                sorted(self._by_concept.items(), key=lambda kv: len(kv[1]), reverse=True)}

    def __len__(self) -> int:
        return len(self.entries)


@functools.lru_cache(maxsize=1)
def get_catalog_index() -> CatalogIndex:
    """Process-wide catalogue, loaded once."""
    return CatalogIndex.load()
