# Implementation Plan: Property Opportunity Hunter

**Branch**: `001-opportunity-hunter` | **Date**: 2026-08-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-opportunity-hunter/spec.md`

## Summary

Build a production-deployable, single-user real-estate opportunity hunter. It periodically collects sales and rental listings from inmoup.com.ar (Argentina) by fetching the site's server-rendered pages and parsing the embedded schema.org JSON-LD, normalizes and stores listings in SQLite with provenance and append-only price history, computes per-zone (barrio) sales and rental baselines (median price, median rent, median price-per-m²), trains a machine-learning valuation model (gradient-boosted regression) on the collected sale listings and produces a versioned, quality-scored market value estimate per active listing, applies configurable opportunity rules (model-based undervaluation, rental yield, recent price drop), optionally enriches descriptions and writes a digest narrative through an OpenAI-compatible language service, and emails the user a digest of newly detected opportunities. Runs as a scheduled job inside a Docker container, with a CLI for manual runs. Collection is deliberately polite (throttled, personal-use, respects terms) per the constitution.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: httpx (HTTP), parsel (XPath/CSS extraction), pydantic v2 (schemas/validation), APScheduler (in-process scheduling), python-dotenv (config), scikit-learn (`HistGradientBoostingRegressor` valuation model), stdlib `sqlite3`, `smtplib`/`email` (SMTP delivery), stdlib `logging` with a JSON formatter (observability), `pytest` (tests), `Docker` (deployment). The LLM client is a thin httpx wrapper over an OpenAI-compatible chat-completions endpoint — no extra SDK dependency.

**Storage**: SQLite (single file, WAL mode) via stdlib `sqlite3` behind a small repository layer. Schema managed by idempotent DDL applied at startup. Raw page snapshots stored gzipped in the same DB for provenance. Trained models serialized (pickle within the same-python-version pinned image) and stored in a DB table with version, training window, dataset size, and quality metrics.

**Testing**: pytest. Unit tests for parsers, normalizers, baseline math, detection rules, feature engineering, model training/persistence, LLM client (stubbed transport), and dedupe; integration tests run the pipeline stages against saved HTML fixtures (real pages captured from inmoup.com.ar) and a small synthetic training set; contract tests validate emitted JSON against versioned schemas.

**Target Platform**: Linux container (`python:3.12-slim`), run as a long-lived scheduler container (APScheduler) and via a `docker run` CLI for manual passes. No web server in v1.

**Project Type**: CLI + scheduled background job (pipeline: ingest → normalize → store → analyze → detect → notify).

**Performance Goals**: A configured daily pass completes within a few hours using polite request pacing; per-zone baseline computation completes in under 1 minute on ~100k stored observations (tracked by SC-003), with model retrain and prediction recomputation in the same run sharing that budget; notification digest sent within minutes of detection.

**Constraints**: <2s default delay between source requests (configurable), request volume bounded per run (configurable max pages per search), single-user, personal-use-only data handling, no secrets committed, source data never republished.

**Scale/Scope**: v1 targets Capital Federal (CABA) departamentos for sale and rent (~8k + listings) as the default configured scope; the collector supports any region/property-type/operation combination the site publishes, targeting up to ~100k stored listings over time.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Production-Ready by Default**: Structured JSON logs per stage, run-level metrics recorded in DB, manual re-run CLI, crash-safe idempotent DDL, and no data loss on partial runs (transactions per stage). PASS.
- **II. Legal & Ethical Scraping**: Throttled, identifiable user-agent, personal-use only, respects `robots.txt` and source terms (no commercial exploitation, no disproportionate load), no contact details harvested beyond what the source already publishes publicly. PASS.
- **III. Data Integrity & Provenance**: Stable identity via source listing URL id, raw page snapshots stored per run, append-only price history, delisting detection without data loss, normalized/validated values. PASS.
- **IV. Test-First Quality**: Tests written for parsers and rules against real captured fixtures; Red-Green-Refactor enforced during implementation. PASS.
- **V. Modular Pipeline, Explicit Contracts**: Stages separated into modules communicating through pydantic schemas; versioned JSON contracts for listing, detection, and notification records; per-stage failure isolation with retries/dead-letter. The ML stage is a first-class module behind the same contracts (model versions and predictions are recorded entities). PASS.

## Project Structure

### Documentation (this feature)

```text
specs/001-opportunity-hunter/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: technical decisions
├── data-model.md        # Phase 1: SQLite schema + domain model
├── quickstart.md        # Phase 1: validation guide
├── contracts/           # Phase 1: versioned JSON schemas
└── tasks.md             # Phase 2 (created by /speckit.tasks)
```

### Source Code (repository root)

```text
src/property_hunter/
├── __init__.py
├── __main__.py              # python -m property_hunter
├── cli.py                   # argparse CLI: run-all, collect, analyze, train, predict, detect, notify, init-db
├── config.py                # env-driven configuration (pydantic-settings style, dotenv)
├── logging_conf.py          # JSON logging setup
├── models.py                # pydantic v2 domain schemas (Listing, PriceObservation, Detection, ...)
├── db.py                    # sqlite repository layer (idempotent DDL, transactions)
├── ingest/
│   ├── __init__.py
│   ├── client.py            # politeness-limited HTTP client (delay, user-agent, retries)
│   ├── inmoup.py            # search URL builder, list/detail page fetch
│   └── extract.py           # JSON-LD + HTML extraction into pydantic schemas
├── normalize.py             # canonicalization, identity keying, validation
├── analyze.py               # per-zone baseline computation
├── ml/
│   ├── __init__.py
│   ├── features.py          # feature engineering from listings (barrio, beds, baths, m², age)
│   ├── train.py             # HistGradientBoostingRegressor training, versioning, quality metrics
│   └── predict.py           # per-listing value estimates + fallback price-per-m² heuristic
├── llm/
│   ├── __init__.py
│   ├── client.py            # OpenAI-compatible chat endpoint client (httpx, config-gated)
│   ├── enrich.py            # amenity/condition tag extraction from descriptions
│   └── narrative.py         # plain-language digest opening summary
├── detect.py                # configurable opportunity rules (model-based undervaluation)
├── notify/
│   ├── __init__.py
│   └── email.py             # SMTP digest delivery with retry/backoff
└── pipeline.py              # stage orchestrator (run-all)
tests/
├── conftest.py
├── fixtures/                # saved real pages from inmoup.com.ar
│   ├── list_caba_deptos_venta_p1.html
│   ├── list_caba_deptos_venta_p2.html
│   └── detail_lezica_4100.html
├── unit/
│   ├── test_extract.py
│   ├── test_normalize.py
│   ├── test_analyze.py
│   ├── test_features.py
│   ├── test_ml.py
│   ├── test_llm.py
│   ├── test_detect.py
│   ├── test_notify.py
│   └── test_db.py
├── integration/
│   └── test_pipeline.py
└── contract/
    ├── test_listing_schema.py
    ├── test_detection_schema.py
    └── test_notification_schema.py
data/                        # gitignored: property_hunter.db (SQLite + gzip snapshots)
Dockerfile
docker-compose.yml
.env.example
.gitignore
pyproject.toml
```

**Structure Decision**: Single Python project (`src/` layout) at repository root with `tests/` split by unit/integration/contract, mirroring the modular pipeline stages as packages under `src/property_hunter/`. A web service is deliberately not included in v1 (single-user, scheduled jobs); adding one later would not require restructuring.

## Complexity Tracking

> No constitution violations — the structure above is justified by constitution principle V (modular pipeline with explicit contracts) and principle IV (fixture-based test discipline).
