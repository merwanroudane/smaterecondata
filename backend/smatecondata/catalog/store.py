"""SQLite-backed catalogue search — bounded memory, any catalogue size.

Replaces the in-memory inverted index on the request hot path. That index was
measured at **393 MB resident** for 43,907 series:

    86.9 MB  postings list (1.35M tuples)
    72.0 MB  tokenisation
    64.2 MB  43,907 Pydantic models
    48.4 MB  parsing the 62 MB worldbank.json
    37.6 MB  per-entry weight dicts

On a 512 MB box that is the whole budget before FastAPI, the providers or a
single response exist. It also does not scale: the spec targets 331k, 500k and
eventually 1M+ series, and this approach grows linearly in RAM.

SQLite FTS5 inverts the trade. The index lives on disk, the query returns a
bounded candidate set, and a search costs kilobytes regardless of whether the
catalogue holds 40 thousand rows or a million.

Ranking keeps the behaviour the in-memory version earned:

1. exact provider code wins outright;
2. multilingual concept aliases are translated to English before matching, so
   Arabic and French rank the English catalogue as well as English does;
3. FTS5 bm25 with per-column weights (name far above description), then a
   title-coverage and title-prefix bonus applied in Python over the small
   candidate set.
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.models import Frequency, IndicatorMetadata, Language, SearchHit
from ..search.aliases import get_concept_store
from ..search.normalize import normalize

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "indicators.db"

# bm25 column weights, in the order the FTS5 table declares them:
# provider, code, name, description, category, keywords, synonyms.
# A term in the official name matters far more than one buried in a long
# description -- without this, an entry whose description merely mentions
# "GDP per capita" outranks the series actually called that.
BM25_WEIGHTS = (0.0, 3.0, 10.0, 1.0, 2.0, 3.0, 2.0)

# An exact provider code is a categorical match, so it scores in a band the
# textual ranker cannot reach (its ceiling is a few hundred).
EXACT_CODE_SCORE = 10_000.0

_CODE_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{2,}$")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "of", "and", "for", "in", "to", "a", "by", "on", "at", "with",
    "from", "between", "since", "during", "over", "show", "give", "me",
    "total", "all", "data", "index", "annual", "value",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in _STOP]


@functools.lru_cache(maxsize=1)
def _geography_tokens() -> frozenset[str]:
    """Every token that names a country or region, in any of the three languages.

    The catalogue describes *indicators*; it says nothing about who or when.
    Geography and period are resolved by their own modules, so those tokens are
    dropped before the catalogue is queried -- otherwise "Inflation in Algeria
    from 2000 to 2025" matches agricultural price indices for Algeria ahead of
    the inflation series the user asked for.
    """
    from ..search.geography import COUNTRY_ALIASES, REGION_ALIASES

    words: set[str] = set()
    for aliases in (*COUNTRY_ALIASES.values(), *REGION_ALIASES.values()):
        for alias in aliases:
            words.update(_TOKEN.findall(normalize(alias, Language.EN)))
            words.update(_TOKEN.findall(alias.lower()))
    return frozenset(w for w in words if len(w) > 1)


@functools.lru_cache(maxsize=1)
def _country_words() -> frozenset[str]:
    """Country and region names that are a single unambiguous word.

    Used to spot a *title* that is baked to one country -- FRED carries
    "Inflation, consumer prices for Japan" as its own series. Multi-word
    aliases are deliberately excluded: "Central African Republic" would
    otherwise make "central" a country word and mis-flag "Central bank policy
    rate" as Japanese-style country-specific.
    """
    from ..search.geography import COUNTRY_ALIASES, REGION_ALIASES

    words: set[str] = set()
    for aliases in (*COUNTRY_ALIASES.values(), *REGION_ALIASES.values()):
        for alias in aliases:
            tokens = _TOKEN.findall(normalize(alias, Language.EN))
            if len(tokens) == 1 and len(tokens[0]) > 3:
                words.add(tokens[0])
    return frozenset(words)


def _is_metadata_noise(token: str) -> bool:
    """True for tokens that cannot appear in indicator metadata: years, ISO
    codes and country or region names."""
    return token.isdigit() or token in _geography_tokens()


def _fts_query(terms: Sequence[str]) -> str:
    """Build a safe FTS5 MATCH expression.

    Every token is quoted and prefix-matched. Quoting matters: an unescaped
    token containing FTS5 syntax (a bare ``-`` or ``"``) is a syntax error that
    fails the whole query rather than that one term.
    """
    quoted = [f'"{t}"*' for t in terms if t]
    return " OR ".join(quoted)


@dataclass(frozen=True)
class CatalogRow:
    provider: str
    code: str
    name: str
    description: str | None
    category: str | None
    unit: str | None
    frequency: str | None
    source_url: str | None

    def to_metadata(self) -> IndicatorMetadata:
        return IndicatorMetadata(
            provider=self.provider,
            series_id=self.code,
            title=self.name,
            description=self.description or None,
            unit=self.unit or None,
            frequency=Frequency.coerce(self.frequency),
            topic=self.category or None,
            source_name=self.provider,
            source_reference=self.source_url or None,
        )


class CatalogStore:
    """Read-only SQLite catalogue. One connection per thread."""

    _warned_missing = False

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self._local = threading.local()

    # -- connection -------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection | None:
        """Thread-local read-only connection, or None when unavailable.

        Returns None rather than raising so a missing or unbuilt database
        degrades search instead of taking the endpoint down.
        """
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        if not self.db_path.exists():
            # Say so once, loudly and with the fix. The database is generated,
            # not committed, so a deploy that skipped the build step would
            # otherwise just return no catalogue results and look like a
            # ranking bug.
            if not CatalogStore._warned_missing:
                CatalogStore._warned_missing = True
                logger.warning(
                    "catalogue database missing at %s -- catalogue search is "
                    "disabled. Build it with: python scripts/build_catalog_db.py",
                    self.db_path,
                )
            return None
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            return conn
        except sqlite3.Error as exc:
            logger.warning("catalogue database unavailable: %s", exc)
            return None

    @property
    def available(self) -> bool:
        conn = self.connection
        if conn is None:
            return False
        try:
            return bool(conn.execute("SELECT 1 FROM indicators LIMIT 1").fetchone())
        except sqlite3.Error:
            return False

    # -- reads ------------------------------------------------------------

    def count(self) -> int:
        conn = self.connection
        if conn is None:
            return 0
        try:
            return int(conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0])
        except sqlite3.Error:
            return 0

    def provider_counts(self) -> dict[str, int]:
        conn = self.connection
        if conn is None:
            return {}
        try:
            rows = conn.execute(
                "SELECT provider, COUNT(*) c FROM indicators GROUP BY provider "
                "ORDER BY c DESC"
            ).fetchall()
        except sqlite3.Error:
            return {}
        return {r["provider"]: r["c"] for r in rows}

    def topic_counts(self, limit: int = 40) -> dict[str, int]:
        conn = self.connection
        if conn is None:
            return {}
        try:
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(category,''),'Uncategorised') t, COUNT(*) c "
                "FROM indicators GROUP BY t ORDER BY c DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error:
            return {}
        return {r["t"]: r["c"] for r in rows}

    def frequency_counts(self) -> dict[str, int]:
        conn = self.connection
        if conn is None:
            return {}
        try:
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(frequency,''),'unknown') f, COUNT(*) c "
                "FROM indicators GROUP BY f ORDER BY c DESC"
            ).fetchall()
        except sqlite3.Error:
            return {}
        return {r["f"]: r["c"] for r in rows}

    def get(self, provider: str, code: str) -> IndicatorMetadata | None:
        conn = self.connection
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT provider, code, name, description, category, unit, "
                "frequency, raw_metadata FROM indicators "
                "WHERE provider = ? AND code = ? LIMIT 1",
                (provider, code),
            ).fetchone()
        except sqlite3.Error:
            return None
        return self._row_to_metadata(row) if row else None

    @staticmethod
    def _row_to_metadata(row: sqlite3.Row) -> IndicatorMetadata:
        source_url = None
        raw = row["raw_metadata"] if "raw_metadata" in row.keys() else None
        if raw:
            try:
                source_url = (json.loads(raw) or {}).get("source_url")
            except (json.JSONDecodeError, TypeError):
                source_url = None
        return CatalogRow(
            provider=row["provider"],
            code=row["code"],
            name=row["name"],
            description=row["description"],
            category=row["category"],
            unit=row["unit"] if "unit" in row.keys() else None,
            frequency=row["frequency"] if "frequency" in row.keys() else None,
            source_url=source_url,
        ).to_metadata()

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
        """Bounded search. Never loads more than ``candidate_limit`` rows."""
        conn = self.connection
        if conn is None or not query.strip():
            return []

        raw = query.strip()
        hits: list[SearchHit] = []
        seen: set[tuple[str, str]] = set()

        # 1. Exact provider code — decisive, and a single indexed lookup.
        if _CODE_LIKE.match(raw):
            try:
                rows = conn.execute(
                    "SELECT provider, code, name, description, category, unit, "
                    "frequency, raw_metadata FROM indicators "
                    "WHERE code = ? COLLATE NOCASE LIMIT 5",
                    (raw,),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                key = (row["provider"], row["code"])
                seen.add(key)
                # Identifier equality, not a better text score. It belongs in
                # its own band, far enough above the textual ceiling that no
                # future ranking bonus can ever overtake a code the user typed.
                hits.append(SearchHit(metadata=self._row_to_metadata(row),
                                      score=EXACT_CODE_SCORE, matched_on=["code"]))

        # 2. Translate the query through the concept store. The catalogue is
        #    written in English; an Arabic or French query shares no tokens
        #    with it, so without this those languages cannot match at all.
        concepts = get_concept_store().find_in_query(query, language)
        normalised = normalize(query, language)
        terms = _tokens(normalised)
        matched_concepts: list[str] = []

        # Secondary aliases of the matched concept -- what the concept *means*,
        # as opposed to what the user happened to type. They rank rather than
        # retrieve: see the alias-overlap bonus below.
        context_tokens: set[str] = set()

        if concepts:
            english: list[str] = []
            for concept in concepts[:2]:
                aliases = concept.aliases.get("en") or ()
                english.append(aliases[0] if aliases else concept.label)
                matched_concepts.append(f"concept:{concept.key}")
                for alias in aliases[1:]:
                    context_tokens.update(_tokens(normalize(alias, Language.EN)))
            translated = _tokens(normalize(" ".join(english), Language.EN))
            if translated:
                # Search the catalogue for the INDICATOR only.
                #
                # A full request carries country names and years -- "Inflation
                # in Algeria from 2000 to 2025" -- and none of that appears in
                # indicator metadata. Feeding the whole sentence to FTS lets
                # "algeria" and "2000" pull in agricultural price indices ahead
                # of the inflation series. Geography and period have their own
                # resolvers, so those tokens are dropped here.
                #
                # What is NOT dropped is the rest of what the user typed. The
                # concept key for "inflation consumer prices" is just
                # `inflation`, and translating to the concept alone discards
                # the two words that separate consumer-price inflation from
                # food-price and wholesale-price inflation. The catalogue query
                # is the concept plus every remaining describing word.
                extra = [t for t in terms
                         if t not in translated and not _is_metadata_noise(t)]
                terms = translated + extra

        if not terms:
            return hits[:limit]

        # 3. Bounded FTS5 lookup. LIMIT is the memory guarantee: whatever the
        #    catalogue size, at most `candidate_limit` rows reach Python.
        candidate_limit = max(limit * 5, 40)
        sql = (
            "SELECT i.provider, i.code, i.name, i.description, i.category, "
            "       i.unit, i.frequency, i.raw_metadata, "
            "       bm25(indicators_fts, ?, ?, ?, ?, ?, ?, ?) AS rank "
            "FROM indicators_fts f "
            "JOIN indicators i ON i.id = f.rowid "
            "WHERE indicators_fts MATCH ? "
        )
        params: list[Any] = [*BM25_WEIGHTS, _fts_query(terms)]
        if providers:
            sql += f"AND i.provider IN ({','.join('?' * len(providers))}) "
            params.extend(providers)
        if topic:
            sql += "AND i.category = ? COLLATE NOCASE "
            params.append(topic)
        sql += "ORDER BY rank LIMIT ?"
        params.append(candidate_limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            logger.warning("catalogue search failed: %s", exc)
            return hits[:limit]

        query_tokens = set(terms)
        # Which countries the user actually named, kept from the raw query --
        # `terms` has had them stripped out by now.
        query_countries = set(_tokens(normalised)) & _country_words()

        for row in rows:
            key = (row["provider"], row["code"])
            if key in seen:
                continue
            seen.add(key)

            # bm25 returns a negative number; smaller is better.
            score = -float(row["rank"]) * 10.0
            matched = list(matched_concepts)

            title = normalize(row["name"] or "", Language.EN)
            title_tokens = set(_tokens(title))
            if title_tokens and query_tokens:
                covered = len(query_tokens & title_tokens) / len(query_tokens)
                if covered:
                    score += 60.0 * covered * covered
                    phrase = " ".join(terms)
                    if phrase and phrase in title:
                        score += 90.0
                        matched.append("title-phrase")
                        if title.startswith(phrase):
                            score += 70.0
                            matched.append("title-prefix")
                    # Shorter titles are more specific at equal coverage.
                    score += 12.0 / (1 + len(title_tokens))
                matched.extend(sorted(query_tokens & title_tokens)[:3])

            # The concept's other aliases say what it means. A bare query of
            # "inflation" matches an ECB dataflow literally titled "Inflation"
            # and the World Bank's "Inflation, consumer prices (annual %)"
            # equally well on the typed word alone; the concept knows that
            # inflation means consumer prices, and that is what breaks the tie.
            overlap = context_tokens & title_tokens - query_tokens
            if overlap:
                score += 18.0 * min(len(overlap), 3)
                matched.append("concept-alias")

            # Some providers bake the country into the series -- FRED ships
            # "Inflation, consumer prices for Japan" as its own entry. Such a
            # series is the best possible answer when it is that country being
            # asked about, and the wrong answer otherwise, however well the
            # rest of the title matches.
            title_countries = title_tokens & _country_words()
            if title_countries:
                if title_countries & query_countries:
                    score += 45.0
                    matched.append("country-specific")
                else:
                    score -= 60.0

            hits.append(SearchHit(metadata=self._row_to_metadata(row),
                                  score=round(score, 4),
                                  matched_on=matched[:6]))

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]


_store: CatalogStore | None = None


def get_catalog_store() -> CatalogStore:
    global _store
    if _store is None:
        _store = CatalogStore()
    return _store


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


_ARRAY_KEY = re.compile(r'"indicators"\s*:\s*\[')


def iter_indicator_objects(text: str) -> Iterable[dict]:
    """Yield the objects of a provider file's ``indicators`` array, one at a time.

    ``json.loads`` on the whole document costs about 200 MB for the 62 MB World
    Bank file, because every one of its 25,000 indicator objects exists at once
    before a single row is written. Decoding them individually keeps only the
    file text and the current object alive, which is what lets the build step
    run inside the same 512 MB budget as the service.

    Falls back to nothing when the file is not shaped as expected; the caller
    then retries with a whole-document parse.
    """
    match = _ARRAY_KEY.search(text)
    if not match:
        return

    decoder = json.JSONDecoder()
    index = match.end()
    length = len(text)

    while index < length:
        while index < length and text[index] in " \t\r\n,":
            index += 1
        if index >= length or text[index] == "]":
            return
        try:
            obj, index = decoder.raw_decode(text, index)
        except ValueError:
            return
        if isinstance(obj, dict):
            yield obj


def iter_metadata_rows(metadata_dir: Path) -> Iterable[tuple]:
    """Stream provider metadata into row tuples.

    One provider file at a time, released before the next is read, and one
    indicator at a time within a file, so building the database never holds a
    whole catalogue in memory.
    """
    from .index import PROVIDER_FILES, SOURCE_URLS

    for stem, provider in PROVIDER_FILES.items():
        path = metadata_dir / f"{stem}.json"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue

        objects: Iterable[dict] = iter_indicator_objects(text)
        first = next(iter(objects), None)
        if first is None:
            # Unexpected shape -- fall back to a full parse rather than
            # silently indexing nothing.
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning("skipping %s: %s", path, exc)
                continue
            objects = [o for o in (payload.get("indicators") or [])
                       if isinstance(o, dict)]
            del payload
        else:
            objects = itertools.chain((first,), objects)

        for raw in objects:
            code = str(raw.get("code") or raw.get("id") or "").strip()
            name = str(raw.get("name") or "").strip()
            if not code or not name:
                continue

            aliases = raw.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]

            source_url = raw.get("source_url") or raw.get("url")
            if not source_url:
                template = SOURCE_URLS.get(provider, "")
                source_url = (
                    template.format(code=code) if "{code}" in template else template
                )

            yield (
                provider,
                code,
                name,
                str(raw.get("description") or "")[:4000],
                str(raw.get("category") or ""),
                str(raw.get("unit") or ""),
                str(raw.get("frequency") or ""),
                " ".join(str(a) for a in aliases[:12]),
                "",
                json.dumps({"source_url": source_url}, ensure_ascii=False),
            )
        del objects, text


def build_database(db_path: Path | None = None,
                   metadata_dir: Path | None = None,
                   batch_size: int = 2000) -> int:
    """(Re)build the catalogue database from the bundled provider metadata."""
    from .index import METADATA_DIR

    db_path = db_path or DB_PATH
    metadata_dir = metadata_dir or METADATA_DIR
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT,
                subcategory TEXT,
                unit TEXT,
                frequency TEXT,
                coverage TEXT,
                start_date TEXT,
                end_date TEXT,
                keywords TEXT,
                synonyms TEXT,
                popularity INTEGER DEFAULT 0,
                last_updated TEXT,
                raw_metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, code)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS indicators_fts USING fts5(
                provider, code, name, description, category, keywords, synonyms,
                content='indicators', content_rowid='id'
            );
            """
        )
        conn.execute("DELETE FROM indicators")
        conn.execute("INSERT INTO indicators_fts(indicators_fts) VALUES('delete-all')")

        inserted = 0
        batch: list[tuple] = []
        for row in iter_metadata_rows(metadata_dir):
            batch.append(row)
            if len(batch) >= batch_size:
                inserted += _insert(conn, batch)
                batch.clear()
        if batch:
            inserted += _insert(conn, batch)

        conn.execute("INSERT INTO indicators_fts(indicators_fts) VALUES('rebuild')")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ind_code ON indicators(code)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ind_provider ON indicators(provider)"
        )
        conn.commit()
        conn.execute("VACUUM")
        return inserted
    finally:
        conn.close()


def _insert(conn: sqlite3.Connection, batch: list[tuple]) -> int:
    conn.executemany(
        "INSERT OR IGNORE INTO indicators "
        "(provider, code, name, description, category, unit, frequency, "
        " keywords, synonyms, raw_metadata) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        batch,
    )
    return len(batch)
