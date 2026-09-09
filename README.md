<p align="center">
  <img src="packages/frontend/public/favicon.svg" width="80" height="80" alt="SmatEconData logo" />
</p>

<h1 align="center">SmatEconData</h1>

<p align="center">
  <strong>Economic data, easier to find.</strong><br/>
  Search in Arabic, English or French — get a documented, refreshable dataset, not just a chart.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-F26B4F?style=flat-square" alt="AGPL-3.0 License" /></a>
  <img src="https://img.shields.io/badge/Python-3.10+-2AAE9B?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/MCP-Server-F2B84B?style=flat-square" alt="MCP Server" />
  <img src="https://img.shields.io/badge/Search-AR%20%C2%B7%20EN%20%C2%B7%20FR-4C8BF5?style=flat-square" alt="Trilingual" />
</p>

<p align="center">
  Built by <strong>Dr Merwan Roudane</strong> &middot;
  <a href="https://github.com/merwanroudane/smaterecondata">github.com/merwanroudane/smaterecondata</a>
</p>

---

## What this is

SmatEconData is a **research-grade economic-data workspace**. You describe the
data you need in ordinary language, verify the definitions it picked, and get a
clean, documented, refreshable dataset with full source provenance — without
navigating half a dozen statistical portals.

The product it optimises for is not "find me a series". It is:

> **a research-ready dataset**, with metadata, descriptive statistics, a
> missing-data analysis, citations, a transformation log and a recipe that
> rebuilds it later.

### What it is not

This is **not an econometric modelling platform**. It does not estimate ARDL,
VAR, VECM, GMM, panel regressions, DSGE, causal models or forecasts. Descriptive
statistics and transparent data preparation only — that boundary is deliberate.

---

## Highlights

| | |
|---|---|
| **Search first, configure later** | `Inflation in Algeria from 2000 to 2025` returns data in one action. No indicator codes, no provider picker, no wizard. |
| **Native Arabic / English / French** | Not a UI translation — the discovery engine itself is trilingual, and works with **no LLM key**. |
| **Ambiguity is surfaced, never guessed** | "inflation" offers CPI vs deflator vs core; "debt" offers central vs general government vs external. |
| **Nothing is invented** | Gaps stay gaps. No interpolation, no imputation, no model-generated observations. |
| **Proof-carrying datasets** | Every exported column traces back to provider, series ID, official title, unit, coverage, retrieval time and a citation. |
| **Excel is a first-class output** | An eight-sheet workbook, not a CSV dump. |
| **Reproducible** | Every dataset has a recipe with a stable hash; re-running it creates a new version and a change summary. |

---

## Quick start

```bash
git clone https://github.com/merwanroudane/smaterecondata.git
cd smaterecondata
python -m pip install -r backend/requirements.txt
npm install
python scripts/build_catalog_db.py     # index the bundled catalogue, ~9s
```

Set the one required secret:

```bash
export JWT_SECRET=$(openssl rand -hex 32)
```

Run it:

```bash
npm run dev
```

Frontend on `http://localhost:5173`, backend on `http://localhost:3001`.

> **No provider API key is needed to start.** The World Bank connector
> works without one; FRED and UN Comtrade need keys for their own data.

> **No LLM API key is needed.** Search, dataset assembly, validation,
> statistics and every export are deterministic. Setting `OPENROUTER_API_KEY`
> (or pointing `LLM_PROVIDER` at a local model) only adds the optional
> natural-language assistant on top.

---

## Trilingual search

These three queries resolve to the same concept and the same official series:

```text
GDP per capita in Algeria
PIB par habitant en Algerie
الناتج المحلي الإجمالي للفرد في الجزائر
```

The pipeline is deterministic: Unicode normalisation → language-specific
folding (Arabic alef/hamza/ta-marbuta, tatweel, Arabic-Indic digits; French
elision and accents; English acronyms and plurals) → geography and period
extraction → multilingual concept aliases → lexical and fuzzy ranking.

Localised aliases are a **discovery aid only**. The series returned always keeps
its official provider title, code, unit and definition.

```bash
curl -s localhost:3001/api/v1/parse \
  -H 'content-type: application/json' \
  -d '{"query":"التضخم والبطالة في الجزائر من 2000 إلى 2025"}'
```

```json
{
  "understanding": {
    "language": "ar",
    "iso3": ["DZA"],
    "concept_keys": ["unemployment", "inflation"],
    "period": "2000-2025",
    "frequency": "annual",
    "needs_clarification": ["unemployment", "inflation"]
  }
}
```

---

## The Excel workbook

| Sheet | Contents |
|---|---|
| `Data` | The dataset. Blank cells are genuine gaps, highlighted, never filled. |
| `Metadata` | Provider, series ID, official title, definition, unit, frequency, coverage. |
| `Descriptive_Stats` | Count, missing, mean, median, sd, quartiles, IQR, CV, skewness, kurtosis. |
| `Missing_Data` | Coverage and gaps by variable and by country, including longest gap and where. |
| `Sources` | Provenance plus a ready-to-paste citation per column, with live hyperlinks. |
| `Transformations` | Every operation applied, in order, with parameters. |
| `Query` | The original request, the resolved specification and the quality report. |
| `README` | What each sheet means. |

Frozen identifier columns, autofilters, per-column number formats, no merged
cells inside the data table.

---

## Reproducibility

Every dataset yields a recipe:

```yaml
recipe_version: '1.0'
name: maghreb_macro_2000_2012
recipe_hash: 1e0715f972d5f366
geographies: [DZA, MAR, TUN]
period: {start_year: 2000, end_year: 2012}
frequency: annual
series:
  - {alias: gdp_per_capita_usd, provider: world_bank, series_id: NY.GDP.PCAP.CD}
  - {alias: inflation_pct, provider: imf, series_id: PCPIPCH}
outputs: [xlsx, html]
```

The hash covers only the request-defining fields, so rebuilding tomorrow gives
the same hash — which makes "did the definition change?" answerable. Refreshing
re-queries the providers, rebuilds, revalidates and creates a **new version**;
the previous snapshot is never overwritten.

---

## Research bundle

One click produces a ZIP containing `data.xlsx`, `data.csv`, `data.parquet`,
`report.html`, `metadata.csv`, `sources.csv`, `transformations.json`,
`dataset_recipe.yaml` and an auto-generated `README.md` explaining what the data
is, where it came from, what was done to it and how to refresh it.

---

## Automatic profiling

After a dataset is built, `Run Automatic Data Profiling` produces a
data-quality EDA report in **Quick** or **Full** mode, exportable as HTML or
JSON. Above the generic report sits an economic-data summary that a generic
profiler cannot infer: which column is the geography, which is the period,
whether the panel is balanced, duplicate country-period keys, and coverage by
variable and by country.

Profiling is **optional and non-blocking by design**. If the backend is absent
or fails, the endpoint still returns 200 with a status and the economic
summary — a third-party failure never costs you the data you retrieved.

```bash
pip install -U fg-data-profiling
```

> **Known upstream issue:** `fg-data-profiling` 4.19.1 imports `pkg_resources`,
> which setuptools removed in v81. If profiling reports itself unavailable for
> that reason, `pip install "setuptools<81"` restores it. Everything else in
> SmatEconData is unaffected either way.

---

## Search means get data

Type a request, press Search, get the dataset. No cart, no provider picker, no
indicator code, and the country is never asked for twice.

```bash
curl -s localhost:3001/api/v1/instant-dataset   -H 'content-type: application/json'   -d '{"query":"التضخم في الجزائر من 2000 إلى 2025"}'
```

One call parses the request, resolves the official series, retrieves the
observations, builds and validates the dataset, and returns rows plus
provenance:

```json
{
  "resolution": [{
    "provider": "world_bank", "series_id": "FP.CPI.TOTL.ZG",
    "official_title": "Inflation, consumer prices (annual %)",
    "confidence": 0.97, "reason": "recommended series for this concept"
  }],
  "dataset": { "rows": 26, "columns": ["geography","iso3","period","inflation_pct"] },
  "warnings": ["2025 is not yet available from this source; data runs to 2024."]
}
```

`POST /api/v1/instant-dataset/from-selection` runs the **same engine** for the
Data Cart, so the cart cannot drift into a second, differently-behaving
pipeline.

The Data Cart remains — as an optional way to collect exact series across
several searches. It is never a toll gate between you and the data you just
searched for.

---

## Interface

**Eight search modes over one discovery engine** (spec 0B). Quick Search is the
default and stays zero-friction; the rest are progressive disclosure behind
tabs — Guided (a skippable six-step builder), Advanced (expert filters), Browse
Catalog, Geography-first with region presets, Source-first, Batch (paste a list,
resolve every line at once), and Exact-code. A mode changes how you *express* a
request, never how it resolves.

**Data Cart** (spec 0D). Search for one indicator, add it, keep searching, then
build one dataset from everything collected. Countries, period, frequency,
shape and provider are *cart-level* settings, so "use the Maghreb" or "make it
2010–2023" is one click for every series rather than an edit per row. The cart
persists across reloads.

**Dataset Workspace** (spec 0E) — eight panels over the built dataset: Data,
Metadata, Descriptive statistics, Profiling, Missing data, Charts, Sources &
provenance, Export. Each loads only when opened. A gap renders as a hatched
"missing" cell, never as a zero.

**Full trilingual interface** (spec 0O). Arabic, English and French UI
dictionaries, with Arabic switching the document to `dir="rtl"` — the layout
mirrors through CSS logical properties, with no RTL-specific rules. The
interface language is independent of the search language: a French UI still
takes an Arabic query. The language control is separate from the theme control,
and the UI language is auto-detected from the browser with a manual override.

**Theme engine** (spec 0H) with four light presets — Sunrise Research, Mint Lab,
Lavender Paper, Sky Citrus — plus a polished dark mode and a `System` option.
Density and decorative motion are separate toggles and every preference
persists. Nothing hard-codes a colour: components read semantic tokens, so a
new palette is a block of CSS variables rather than a component rewrite.

**Animated hero** (spec 0G.1) telling the
`Question → Discover → Select → Build Dataset → Profile → Export` story in
inline SVG and CSS — no animation library, no external asset. It honours
`prefers-reduced-motion`, pauses when the tab is hidden, and never blocks
typing.

---

## Catalogue

Search runs over the **bundled provider catalogue** — 43,907 series from ten
providers shipped in `backend/data/metadata`, indexed into a SQLite **FTS5**
database. Ranking is hybrid, in the spec's own order: exact provider code
first, then multilingual concept aliases, then BM25 with per-column weights
and a title-coverage bonus applied over a bounded candidate set.

The database is generated, not committed. Build it once before serving:

```bash
python scripts/build_catalog_db.py     # 43,907 series, ~9s, 59 MB on disk
```

The catalogue lives on disk and a query reads a bounded number of rows, so
searching costs kilobytes whether the catalogue holds 44 thousand series or a
million. The previous in-memory index cost **393 MB resident** at 43,907
series and grew linearly — see [Deploying](#deploying).

Because the catalogue is written in English, an Arabic or French query is
translated through its resolved concept before the lexical stage — so
`معدل البطالة` and `taux de chômage` rank the same unemployment series, at the
same scores, as `unemployment rate`.

```bash
curl -s 'localhost:3001/api/v1/indicators/search?q=%D8%A7%D9%84%D8%AA%D8%B6%D8%AE%D9%85&limit=2'
```

### Scale, reported honestly

Spec 0.1A sets three thresholds — a **331,000** legacy floor, a **500,000**
expansion target and a **1,000,000** stretch target — and one hard rule:

> Never hard-code `500K+`, `800K+`, or `1M+` into marketing/UI text. Always
> calculate the displayed number from the actual production index.

`CatalogStatsService` computes every figure from the live index and will not
emit a milestone badge the index has not reached. The home page shows whatever
is really there.

**What ships in this repository is below the floor**, and says so:

```json
{
  "searchable_series_count": 43907,
  "display_count": "43,907 indexed series",
  "milestone": "below_floor",
  "meets_legacy_floor": false,
  "next_milestone": { "name": "legacy_floor", "target": 331000,
                      "remaining": 287093, "progress_pct": 13.26 }
}
```

The 331K figure describes a fully-synchronised production catalogue. Its
database (`indicators.db`, ~827 MB) is gitignored upstream and has never been
distributable — anyone cloning starts from the bundled metadata.

### Growing the catalogue

**No key required.** `fetch_open_catalog.py` pulls the open, no-registration
sources — ILOSTAT, the ECB Data Portal and the UN SDG series:

```bash
python scripts/fetch_open_catalog.py
```

**With a free key.** FRED is the largest single source by an order of magnitude
(800k+ series) and is skipped entirely without one:

```bash
export FRED_API_KEY=...   # free: fred.stlouisfed.org/docs/api/api_key.html
python scripts/fetch_all_indicators.py
```

Note that `fetch_all_indicators.py` walks a hand-written list of 36 FRED
categories capped at ~132k series, so even with a key it does not reach the
full FRED catalogue.

The counter, the badge and the milestone all move on their own once the index
does. Nothing needs editing. Re-run `scripts/build_catalog_db.py` after any
fetch script to index what it downloaded.

---

## Deploying

The backend runs in **512 MB** on a Render free instance with a single Uvicorn
worker. Two things make that fit, and both are load-bearing:

**1. Build the catalogue database during the build step.** `indicators.db` is
generated and gitignored, so it must be produced where the code is deployed:

```bash
pip install -r backend/requirements.txt && python scripts/build_catalog_db.py
```

Without it the service still starts and instant search still answers from its
curated mappings, but catalogue search returns nothing and the log says so
once, with the command to fix it. Building peaks around 258 MB — fine in a
build step, which is why it is not done at start-up.

**2. Providers are constructed on demand.** `ProviderGateway` takes a factory
and builds a connector the first time one is actually needed, so answering
"Inflation in Algeria" instantiates World Bank and nothing else. Excel,
profiling and the report writer are imported inside their handlers rather than
at module scope, so a search never pays for `openpyxl`.

Measure any change to the search path before deploying it:

```bash
python scripts/measure_instant_memory.py
```

It reports heap peak, RSS, which providers were constructed and whether any
heavy library reached the hot path, then a verdict against the 512 MB limit.
The reference query currently peaks at **36 MB**.

| | Before | After |
|---|---|---|
| Catalogue search | 393 MB | 0.33 MB |
| Catalogue statistics | 272 MB peak | 14 MB peak |
| Full `instant-dataset` request | over 512 MB — killed | **36 MB peak** |

The regression tests in `backend/tests/test_instant_memory.py` pin the
decisions rather than the megabytes: one provider per clear query, a fallback
built only after the primary fails, and a subprocess check that importing the
search path pulls in no export or profiling library.

---

## REST API

Versioned under `/api/v1`, documented at `/docs`.

| Endpoint | Purpose |
|---|---|
| `POST /parse` | Natural language → transparent "what I understood" |
| `GET /concepts`, `GET /geographies` | Browse the multilingual catalogue |
| `POST /datasets` | Assemble a dataset from resolved series |
| `POST /datasets/{id}/validate` | Integrity report with severity-ranked issues |
| `POST /datasets/{id}/describe` | Descriptive statistics and correlations |
| `GET /datasets/{id}/missingness` | Coverage, gaps and the coverage matrix |
| `GET /datasets/{id}/lineage` | Per-column provenance |
| `POST /datasets/{id}/transform` | Explicit, logged transformations |
| `GET /datasets/{id}/recipe` | YAML or JSON recipe |
| `POST /datasets/{id}/compare/{other}` | Version diff |
| `POST /datasets/{id}/export` | xlsx, csv, json, parquet, feather, dta, html, bundle |
| `GET /catalog/stats` | Live catalogue size, breakdowns and milestone |
| `GET /indicators/search` | Search the provider catalogue (AR/EN/FR) |
| `GET /indicators/{provider}/{id}` | Official metadata for one series |
| `GET /catalog/topics`, `GET /providers` | Catalogue breakdowns |
| `GET /profiling/status` | Whether automatic EDA is available |
| `POST /datasets/{id}/profile` | Run Quick or Full profiling |
| `GET /datasets/{id}/profile/summary` | Panel balance, coverage, column roles |
| `GET /datasets/{id}/profile/report.{html,json}` | Profiling artefacts |

## MCP

The MCP server exposes **granular** tools, not one opaque endpoint: agents can
`build_dataset`, `validate_dataset`, `describe_dataset`,
`summarize_missingness`, `save_dataset_recipe`, `compare_dataset_versions`,
`export_dataset`, `profile_dataset` and more, step by step — 26 tools in all.

```bash
claude mcp add --transport sse smatecondata http://localhost:3001/mcp
```

---

## Data sources

FRED, World Bank, IMF, Eurostat, OECD, BIS, UN Comtrade, Statistics Canada,
ExchangeRate-API, CoinGecko, ChinaMacro.

---

## Testing

```bash
python -m pytest backend/tests/test_multilingual_search.py \
                 backend/tests/test_dataset_pipeline.py \
                 backend/tests/test_recipe_and_bundle.py \
                 backend/tests/test_provider_gateway.py \n                 backend/tests/test_catalog.py \
                 backend/tests/test_profiling.py \
                 backend/tests/test_smatecondata_api.py
```

End-to-end demonstrations:

```bash
python scripts/demo_export.py   # offline, fixture data
python scripts/demo_live.py     # live World Bank data, no API key
```

Branding-cleanup acceptance test (spec 0Q):

```bash
python scripts/brand_migrate.py --check
```

> **Note (Windows):** the Statistics Canada tests import `curl_cffi`, which can
> crash the interpreter with a native dialog on Windows. Run the suites above
> directly rather than the whole `backend/tests` directory on that platform.

---

## Licence and attribution

SmatEconData is released under the **GNU AGPL-3.0** (see [LICENSE](LICENSE)).

It is a derivative of an existing AGPL-3.0 project; the upstream copyright
notice and full attribution are preserved in [NOTICE](NOTICE) and [LICENSE](LICENSE)
as the licence requires. Because AGPL-3.0 is a network copyleft licence, anyone
who interacts with a deployed instance is entitled to the corresponding source.

The SmatEconData product identity, the trilingual discovery engine, the
dataset / validation / provenance layer and the export system are the work of
Dr Merwan Roudane.

If you use SmatEconData in research, cite the underlying data providers — the
`Sources` sheet and the bundle README generate those citations for you.
