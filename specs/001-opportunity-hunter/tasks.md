---

description: "Task list for the property opportunity hunter feature implementation"

---

# Tasks: Property Opportunity Hunter

**Input**: Design documents from `/specs/001-opportunity-hunter/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are REQUIRED by the project constitution (principle IV — Test-First). Tests for each story are written first and observed to fail before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story. Tests are listed before implementation for every story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- Project package root: `src/property_hunter/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create repository scaffolding: `pyproject.toml` (project `property-hunter`, Python 3.12, `src/` layout), `.gitignore` (`.env`, `data/`, `__pycache__/`, `.venv/`, `.specify/`), `.env.example` with all config keys (scope, delay, thresholds, SMTP, alert email)
- [x] T002 Implement config module `src/property_hunter/config.py` (env-driven dataclass/pydantic config: scope, COLLECT_DELAY_SECONDS, MAX_PAGES_PER_SEARCH, thresholds, SMTP_*, ALERT_EMAIL, DB_PATH; no secret defaults)
- [x] T003 Implement JSON logging setup `src/property_hunter/logging_conf.py` (stdlib `logging` + custom JSON `Formatter`, run_id binding)
- [x] T004 [P] Create `Dockerfile` (python:3.12-slim) and `docker-compose.yml` (app service with `data/` volume, scheduler entrypoint; manual run via `docker compose run`)
- [x] T005 [P] Add `__main__.py` and CLI skeleton `src/property_hunter/cli.py` (argparse subcommands: init-db, collect, analyze, train, predict, detect, notify, run-all)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T006 Implement pydantic domain schemas in `src/property_hunter/models.py` mirroring `contracts/*.json` (ListingRecord, Signal, DetectionRecord, NotificationRecord, BaselineRecord)
- [x] T007 Implement SQLite repository `src/property_hunter/db.py`: connection factory (WAL, foreign keys), idempotent DDL for all tables in data-model.md (`runs`, `pages`, `listings`, `observations`, `price_history`, `zones`, `baselines`, `model_versions`, `predictions`, `detections`, `notifications`, `settings`), CRUD/repo methods per table
- [x] T008 Implement `init-db` wiring and startup DDL in `src/property_hunter/cli.py`

**Checkpoint**: `uv run property_hunter init-db` creates `data/property_hunter.db` with all tables. Foundation ready.

---

## Phase 3: User Story 1 - Collect and store property listings (Priority: P1) 🎯 MVP

**Goal**: Ingest inmoup.com.ar listings, normalize, store with provenance and price history.

**Independent Test**: Run a collect pass against fixture pages; verify listings persisted, no duplicates on re-run, price changes recorded.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US1] Unit test for JSON-LD extraction from list-page fixture `tests/fixtures/list_caba_deptos_venta_p1.html` in `tests/unit/test_extract.py` (URL, price, currency, beds, baths, area, barrio, region, agency per item)
- [x] T010 [P] [US1] Unit test for JSON-LD extraction from detail-page fixture `tests/fixtures/detail_lezica_4100.html` in `tests/unit/test_extract.py` (street address, total area, datePosted, geo, additionalProperty)
- [x] T011 [P] [US1] Unit test for pagination: page 2 fixture `tests/fixtures/list_caba_deptos_venta_p2.html` yields distinct listings and does not repeat page-1 ids, in `tests/unit/test_extract.py`
- [x] T012 [P] [US1] Unit test for normalize/identity in `tests/unit/test_normalize.py`: source_listing_id parsed from URL, stable identity, barrio parsed from item name when locality null, price → price_cents, listing with no parseable barrio bucketed under "unknown" within its region (zone-assignment coverage for SC-002)
- [x] T013 [P] [US1] Unit test for dedupe + price history in `tests/unit/test_db.py`: re-observing same price writes no price_history row; price change appends row; delisted listing set `is_active=0` with history preserved
- [x] T014 [P] [US1] Contract test `tests/contract/test_listing_schema.py` validating a ListingRecord instance against `contracts/listing-v1.schema.json`
- [x] T015 [P] [US1] Integration test `tests/integration/test_pipeline.py::test_collect_fixtures`: full collect stage over fixture pages into a temp DB yields expected listings with provenance (pages rows) and no duplicates on second run

### Implementation for User Story 1

- [x] T016 [P] [US1] Implement politeness client `src/property_hunter/ingest/client.py` (httpx; configurable delay between requests, identifiable user-agent, retries with backoff on 429/5xx, timeout)
- [x] T017 [P] [US1] Implement search URL builder + list/detail fetch in `src/property_hunter/ingest/inmoup.py` (build URL from operation/type/region; `?pagina=N` pagination; detail URL from listing URL)
- [x] T018 [P] [US1] Implement JSON-LD + HTML extraction in `src/property_hunter/ingest/extract.py` (parse `application/ld+json` blocks; extract ListingRecord fields per data-model.md; barrio from item name fallback)
- [x] T019 [US1] Implement normalize/identity module `src/property_hunter/normalize.py` (canonical fields, source_listing_id from URL, operation/property_type mapping, price → cents, validation)
- [x] T020 [US1] Implement collect pipeline stage in `src/property_hunter/pipeline.py` (create run, iterate searches/pages, store pages snapshot (gzip), upsert listings, write observations, write price_history deltas, mark unseen active listings as inactive, record run status) — depends on T016–T019
- [x] T021 [US1] Wire `collect` subcommand in `src/property_hunter/cli.py` (network mode + `--offline-fixtures` mode reading `tests/fixtures/`)
- [x] T022 [US1] Add run-level logging + per-run metrics for collect (counts: fetched pages, new listings, price changes, delisted, zone-assignment rate ≥95% for SC-002)

**Checkpoint**: `uv run property_hunter collect --offline-fixtures` populates the DB; re-run produces no duplicates. US1 testable independently.

---

## Phase 4: User Story 2 - Compute zone market baselines & train valuation model (Priority: P1)

**Goal**: Per-zone (barrio+region) sales and rental baselines with sufficiency flag; ML valuation model with versioned, quality-scored per-listing value estimates and fallback.

**Independent Test**: Load known fixtures across two zones; verify medians, price-per-m², counts, insufficient-data flagging; train on a small synthetic set and verify per-listing estimates with version/quality/fallback.

### Tests for User Story 2

- [x] T023 [P] [US2] Unit test `tests/unit/test_analyze.py::test_median_baseline`: two-zone fixture data yields correct median price, median rent, median price-per-m² per operation and property_type
- [x] T024 [P] [US2] Unit test `tests/unit/test_analyze.py::test_insufficient_zone`: zone below MIN_OBSERVATIONS_PER_ZONE flagged `is_sufficient=0` and excluded from scoring data
- [x] T025 [P] [US2] Unit test `tests/unit/test_analyze.py::test_window_immutable`: recompute creates a new baseline row; prior window row preserved
- [x] T026 [P] [US2] Integration test `tests/integration/test_pipeline.py::test_analyze_fixtures`: analyze over a DB seeded from fixtures produces expected baselines

### Implementation for User Story 2

- [x] T027 [US2] Implement baseline computation `src/property_hunter/analyze.py` (query active observations, group by zone×operation×property_type, median helpers, window tracking, sufficiency threshold, upsert zones)
- [x] T028 [US2] Wire `analyze` subcommand in `src/property_hunter/cli.py` with per-zone summary logging
- [x] T052 [P] [US2] Unit test `tests/unit/test_features.py`: feature engineering from listings (barrio one-hot, beds/baths/covered/total m², age) yields expected feature vectors and handles nulls
- [x] T053 [P] [US2] Unit test `tests/unit/test_ml.py::test_train_quality`: training on a small synthetic sale dataset produces a persisted model version with recorded training window, count, R² and MAE; predictions on active listings match expected within tolerance
- [x] T054 [P] [US2] Unit test `tests/unit/test_ml.py::test_fallback`: below ML_MIN_TRAIN_SAMPLES (or failed training) records `is_fallback=1` price-per-m² estimates and no model version
- [x] T055 [P] [US2] Unit test `tests/unit/test_ml.py::test_retrain_supersede`: a new training pass creates a new model version and marks the previous `is_current=0`; predictions reference the new version
- [x] T056 [P] [US2] Integration test `tests/integration/test_pipeline.py::test_train_fixtures`: train+predict over a fixture-seeded DB yields one prediction per active sale listing with a current model version
- [x] T057 [US2] Implement feature engineering `src/property_hunter/ml/features.py` (barrio/type one-hot, numeric beds/baths/covered/total m², age from date_posted; null handling)
- [x] T058 [US2] Implement training + persistence `src/property_hunter/ml/train.py` (HistGradientBoostingRegressor on log1p price, held-out split metrics, model_versions upsert with is_current flag, skip+fallback below ML_MIN_TRAIN_SAMPLES)
- [x] T059 [US2] Implement prediction + fallback `src/property_hunter/ml/predict.py` (recompute estimates for all active sale listings; store predictions rows with model_version_id, is_fallback, feature_importances; fallback = zone median price-per-m² × covered m² when no model)
- [x] T060 [US2] Wire `train` + `predict` subcommands in `src/property_hunter/cli.py` with model/prediction summary logging

**Checkpoint**: Baselines computed for all sufficient zones; insufficient zones flagged; current model version persisted with quality metrics; every active sale listing has a prediction or explicit fallback. US2 testable independently.

---

## Phase 5: User Story 3 - Detect potential opportunities (Priority: P2)

**Goal**: Apply configurable opportunity rules to active sale listings against zone baselines; emit explainable detections.

**Independent Test**: With fixture listings/baselines, verify each rule fires correctly and detection records contain exact numbers.

### Tests for User Story 3

- [x] T029 [P] [US3] Unit test `tests/unit/test_detect.py::test_undervaluation`: listing priced 15% below its model value estimate fires `undervaluation` at 10% threshold; near-threshold boundary cases; signal records model_version_id/is_fallback
- [x] T030 [P] [US3] Unit test `tests/unit/test_detect.py::test_undervaluation_fallback`: listing without a model prediction (fallback estimate only) still scores undervaluation against the fallback value and records `is_fallback=true`
- [x] T061 [P] [US3] Unit test `tests/unit/test_detect.py::test_yield`: listing priced for ≥6% annual yield (zone median rent per m² × covered area) fires `yield`
- [x] T031 [P] [US3] Unit test `tests/unit/test_detect.py::test_price_drop`: price history with ≥5% drop within 30 days fires `price_drop`; older drop does not
- [x] T032 [P] [US3] Unit test `tests/unit/test_detect.py::test_rule_configuration`: enabling only price_drop suppresses undervaluation/yield; threshold changes alter firing
- [x] T033 [P] [US3] Unit test `tests/unit/test_db.py::test_detection_supersede`: new detection for a listing supersedes previous `active` detection
- [x] T034 [P] [US3] Contract test `tests/contract/test_detection_schema.py` validating a DetectionRecord against `contracts/detection-v1.schema.json`
- [x] T035 [P] [US3] Integration test `tests/integration/test_pipeline.py::test_detect_fixtures`: detect over fixture-seeded DB yields expected signals with exact observed/expected values

### Implementation for User Story 3

- [x] T036 [US3] Implement opportunity rules `src/property_hunter/detect.py` (evaluate signals per active sale listing against current baselines, predictions + price history; undervaluation uses the value estimate; configurable enable/threshold per signal; aggregate score; skip insufficient zones)
- [x] T037 [US3] Wire `detect` subcommand in `src/property_hunter/cli.py` (persist detections, supersede prior active detection per listing, log counts)

**Checkpoint**: Detections produced with explainable signals; re-runs supersede rather than duplicate. US3 testable independently.

---

## Phase 6: User Story 4 - Alert the user on new opportunities (Priority: P2)

**Goal**: Email digest of newly detected opportunities with delivery confirmation and retry.

**Independent Test**: Trigger notify with one known new detection; verify a correctly formatted digest is sent and failures retried/recorded.

### Tests for User Story 4

- [x] T038 [P] [US4] Unit test `tests/unit/test_notify.py::test_digest_build`: digest body contains address, price, signals, baseline context, listing link for each detection
- [x] T039 [P] [US4] Unit test `tests/unit/test_notify.py::test_dedupe`: detection already notified for same channel/state is not re-sent
- [x] T040 [P] [US4] Unit test `tests/unit/test_notify.py::test_retry`: SMTP failure triggers backoff retries then `failed` status with last_error recorded
- [x] T041 [P] [US4] Contract test `tests/contract/test_notification_schema.py` validating a NotificationRecord against `contracts/notification-v1.schema.json`
- [x] T042 [P] [US4] Integration test `tests/integration/test_pipeline.py::test_notify_smtp_null`: notify with a stub transport verifies one notification row per detection and no duplicates on re-run

### Implementation for User Story 4

- [x] T043 [US4] Implement email transport `src/property_hunter/notify/email.py` (stdlib `smtplib`/`email`: TLS, auth from config, digest HTML+text body builder, retry with exponential backoff)
- [x] T044 [US4] Wire `notify` subcommand in `src/property_hunter/cli.py` (select new detections not yet notified for channel, record notifications with status, summary logging)
- [x] T062 [P] [US4] Unit test `tests/unit/test_llm.py::test_enrich`: LLM client with a stubbed transport returns amenity tags validated against the fixed tag vocabulary; malformed/missing responses fail open (tags omitted, run continues)
- [x] T063 [P] [US4] Unit test `tests/unit/test_llm.py::test_narrative`: stubbed transport returns a digest narrative that renders in the digest opening; empty/error narrative falls back to the templated opening
- [x] T064 [P] [US4] Unit test `tests/unit/test_llm.py::test_skipped`: without LLM_BASE_URL/LLM_API_KEY the LLM stage is skipped and logs `llm.skipped` without failing the run
- [x] T065 [P] [US4] Unit test `tests/unit/test_llm.py::test_budget_timeout`: per-run enrichment budget (≤1 request per new description) and 30s timeout are enforced
- [x] T066 [P] [US4] Integration test `tests/integration/test_pipeline.py::test_notify_llm_narrative`: notify with a stubbed LLM writes `digest_narrative`/`llm_enriched` on notification rows; without config the digest renders without them
- [x] T067 [US4] Implement LLM client `src/property_hunter/llm/client.py` (httpx OpenAI-compatible chat endpoint; base_url/model/api_key + timeout from env; fail-open on any error/timeout; no key → skipped)
- [x] T068 [US4] Implement description enrichment `src/property_hunter/llm/enrich.py` (extract amenity/condition tags against fixed vocabulary, one request per new description, budgeted, persist `llm_amenity_tags` on listings)
- [x] T069 [US4] Implement digest narrative `src/property_hunter/llm/narrative.py` (one summary request per notify pass; plain Spanish digest opening)
- [x] T070 [US4] Wire LLM stage into `notify`/pipeline in `src/property_hunter/cli.py` + `src/property_hunter/pipeline.py` (config-gated; log skip/errors; never abort notify)

**Checkpoint**: Digest sent for new detections; failures surfaced; no duplicate alerts. US4 testable independently.

---

## Phase 7: Orchestration, Polish & Cross-Cutting Concerns

**Purpose**: End-to-end wiring, deployment, and production hardening

- [x] T045 Implement `run-all` orchestrator in `src/property_hunter/pipeline.py` (collect → analyze → train/predict → detect → notify in one run, shared run_id, per-stage status/error capture, partial-failure handling; train/predict skipped to fallback-safe predictions when data insufficient)
- [x] T046 Add APScheduler daily job entrypoint `src/property_hunter/scheduler.py` (default `cron` daily at configured hour; runs `run-all`)
- [x] T047 Write `README.md` (setup, config reference, usage, politeness/compliance notes per constitution II)
- [x] T048 [P] Wire `.env.example` + docker-compose secrets handling (SMTP creds via env, no committed secrets)
- [x] T049 Add `git`-level docs: ensure `.gitignore` excludes `.env` and `data/`; add `specs/001-opportunity-hunter/` docs as the single source of truth for behavior
- [x] T050 Run `quickstart.md` validation end-to-end (Scenarios A–F) and fix any gaps
- [x] T051 Final review gate: constitution check (polite collection, provenance, test coverage, module contracts), run full test suite, verify Docker build
- [x] T071 Add scale test `tests/unit/test_analyze.py::test_perf_100k`: seed ~100k synthetic observations across zones and assert baseline computation completes in under 60 seconds (SC-003)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion; proceed sequentially P1 → P1 → P2 → P2 (data pipeline order: collect → analyze → train/predict → detect → notify)

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services; services before CLI wiring
- Story complete before moving to next priority

### Parallel Opportunities

- Phase 1 tasks marked [P] can run in parallel
- Phase 2 T006 and T007 can run in parallel (schemas vs DDL), then T008
- All test tasks within a story marked [P] can run in parallel before implementation begins
- Implementation tasks T016–T018 are [P]; T019–T021 follow sequentially

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (collect + store)
4. **STOP and VALIDATE**: `uv run property_hunter collect --offline-fixtures` + no-duplicate re-run
5. Continue incrementally: US2 → US3 → US4, validating each via its independent test

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 collect → Test independently (offline fixtures) → validates MVP data foundation
3. US2 analyze + train/predict → Test independently → zone pricing report + value estimates
4. US3 detect → Test independently → explainable, model-informed opportunities
5. US4 notify (incl. LLM enrichment/narrative) → Test independently → email digests (end-to-end value)
6. Orchestration + scheduler → deployable container

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Tests required by constitution principle IV; verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Politeness defaults (delay ≥2s, bounded pages) are part of the collect stage and must not be bypassed in production paths (constitution II)
