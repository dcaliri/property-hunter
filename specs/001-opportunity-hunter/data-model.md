# Data Model: Property Opportunity Hunter

Phase 1 output of `/speckit.plan`. SQLite schema (WAL mode, foreign keys enforced) with the domain entities from the feature spec.

## Conventions

- All monetary values stored as integer minor units (`price_cents`) with `currency` (v1: always `USD`).
- All timestamps stored as UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SS+00:00`) or unix epoch integers as noted.
- `raw_snapshot` fields store gzipped bytes of the fetched page HTML.
- IDs are monotonically increasing integers; external identity comes from source keys.

## Tables

### runs

One row per pipeline pass (each `run-all` execution writes one `collect`, `analyze`, `detect`, `notify` stage row pointing to the same run).

| column        | type       | notes                                        |
|---------------|------------|----------------------------------------------|
| id            | INTEGER PK | run id (referenced by other tables)          |
| started_at    | TEXT       | UTC ISO-8601                                 |
| finished_at   | TEXT       | nullable; set when run ends                  |
| status        | TEXT       | `running` \| `ok` \| `partial` \| `failed`   |
| trigger       | TEXT       | `scheduled` \| `manual`                      |

### pages

One row per HTTP page fetched during collection, for provenance (constitution III).

| column        | type        | notes                                         |
|---------------|-------------|-----------------------------------------------|
| id            | INTEGER PK  |                                                |
| run_id        | INTEGER FK  | → runs.id                                     |
| url           | TEXT        | fetched URL (with `?pagina=N`)                |
| fetched_at    | TEXT        | UTC ISO-8601                                  |
| status_code   | INTEGER     | HTTP status                                   |
| raw_snapshot  | BLOB        | gzipped HTML                                  |

### listings

Canonical properties. Identity: `source` + `source_listing_id` unique.

| column            | type     | notes                                             |
|-------------------|----------|---------------------------------------------------|
| id                | INTEGER PK |                                                |
| source            | TEXT     | `inmoup`                                         |
| source_listing_id | INTEGER  | `{id}` from `…/inmuebles/{id}/ficha/…`           |
| source_url        | TEXT     | full canonical listing URL                       |
| operation         | TEXT     | `sale` \| `rent`                                 |
| property_type     | TEXT     | `departamento` \| `casa` \| `ph` \| `lote` \| …  |
| street_address    | TEXT     | nullable; from detail page                       |
| barrio            | TEXT     | neighborhood (zone key, part 1); the property's `localidad` per the source (never the agency's address) |
| region            | TEXT     | locality/province (zone key, part 2, e.g. "Capital Federal") |
| lat               | REAL     | nullable; exact map-marker latitude from list-page `coordenadas`, kept for verification/audit |
| lng               | REAL     | nullable; exact map-marker longitude from list-page `coordenadas` |
| beds              | INTEGER  | nullable                                         |
| baths             | INTEGER  | nullable                                         |
| covered_area_m2   | REAL     | nullable; "Superficie" from list page             |
| total_area_m2     | REAL     | nullable; "Superficie Total" from detail page     |
| agency_name       | TEXT     | provider name                                    |
| description       | TEXT     | nullable; listing description text (from detail page / raw snapshot) |
| date_posted       | TEXT     | nullable; from detail page `datePosted`          |
| llm_amenity_tags  | TEXT     | nullable; JSON array of amenity/condition tags (LLM enrichment) |
| llm_tags_updated_at | TEXT   | nullable; UTC ISO-8601                          |
| first_seen_at     | TEXT     | UTC ISO-8601; first observation                 |
| last_seen_at      | TEXT     | UTC ISO-8601; most recent observation           |
| is_active         | INTEGER  | 1 = seen in latest run, 0 = delisted            |
| created_at        | TEXT     | UTC ISO-8601                                    |
| updated_at        | TEXT     | UTC ISO-8601                                    |

UNIQUE(source, source_listing_id); index on (source, is_active); index on (region, barrio).

### observations

One row per listing per run — the current snapshot of an active listing. Enables per-run facts and price-history reconstruction.

| column          | type       | notes                                        |
|-----------------|------------|----------------------------------------------|
| id              | INTEGER PK |                                              |
| run_id          | INTEGER FK | → runs.id                                    |
| listing_id      | INTEGER FK | → listings.id                                |
| price_cents     | INTEGER    | asking price in minor units of currency      |
| currency        | TEXT       | `USD`                                        |
| observed_at     | TEXT       | UTC ISO-8601                                 |
| page_id         | INTEGER FK | → pages.id (provenance)                      |
| is_active       | INTEGER    | 1 = present on source, 0 = seen delisted     |

UNIQUE(run_id, listing_id); index on (listing_id, observed_at).

### price_history

Append-only log of asking-price changes per listing (spec FR-005).

| column       | type       | notes                                   |
|--------------|------------|-----------------------------------------|
| id           | INTEGER PK |                                         |
| listing_id   | INTEGER FK | → listings.id                           |
| old_price_cents | INTEGER | nullable (first observation)            |
| new_price_cents | INTEGER | current price at change                 |
| currency     | TEXT       | `USD`                                   |
| observed_at  | TEXT       | UTC ISO-8601                            |
| run_id       | INTEGER FK | → runs.id                               |

A row is written whenever a listing's current price differs from its previous price (or on first observation).

### zones

Normalized zone keys.

| column   | type      | notes                          |
|----------|-----------|--------------------------------|
| id       | INTEGER PK|                                |
| region   | TEXT      | e.g. "Capital Federal"         |
| barrio   | TEXT      | e.g. "Almagro"                 |

UNIQUE(region, barrio).

### baselines

Immutable per-window zone statistics (spec FR-008, FR-009).

| column              | type      | notes                                  |
|---------------------|-----------|----------------------------------------|
| id                  | INTEGER PK|                                        |
| zone_id             | INTEGER FK| → zones.id                             |
| operation           | TEXT      | `sale` \| `rent`                       |
| property_type       | TEXT      | `departamento` \| `casa` \| …          |
| window_start        | TEXT      | UTC ISO-8601                           |
| window_end          | TEXT      | UTC ISO-8601                           |
| observation_count   | INTEGER   | number of listings in window           |
| is_sufficient       | INTEGER   | 1 = count ≥ threshold, 0 = flagged     |
| median_price_cents  | INTEGER   | nullable when insufficient             |
| median_rent_cents   | INTEGER   | nullable when insufficient (rent op)   |
| median_price_per_m2_cents | INTEGER | nullable when insufficient        |
| computed_at         | TEXT      | UTC ISO-8601                           |

Index on (zone_id, operation, property_type, computed_at).

### model_versions

One row per persisted trained valuation model (spec FR-018, FR-019).

| column                  | type       | notes                                  |
|-------------------------|------------|----------------------------------------|
| id                      | INTEGER PK | model version id                       |
| run_id                  | INTEGER FK | → runs.id (train run)                  |
| trained_at              | TEXT       | UTC ISO-8601                           |
| training_window_start   | TEXT       | UTC ISO-8601                           |
| training_window_end     | TEXT       | UTC ISO-8601                           |
| training_count          | INTEGER    | active sale listings used              |
| r2_score                | REAL       | held-out fit metric                    |
| mae_cents               | INTEGER    | mean absolute error (held-out)         |
| blob                    | BLOB       | serialized estimator                   |
| is_current              | INTEGER    | 1 = latest model, 0 = superseded       |
| notes                   | TEXT       | nullable; e.g. fallback/quality notes  |

Only one row has `is_current = 1` at a time; a new training pass supersedes it.

### predictions

One row per (listing, model_version) — the value estimate used by detection (spec FR-019, FR-021).

| column            | type       | notes                                         |
|-------------------|------------|-----------------------------------------------|
| id                | INTEGER PK |                                               |
| listing_id        | INTEGER FK | → listings.id                                 |
| model_version_id  | INTEGER FK | → model_versions.id (nullable on fallback)    |
| run_id            | INTEGER FK | → runs.id (run that produced the estimate)    |
| predicted_price_cents | INTEGER | ML estimate (or fallback estimate)          |
| is_fallback       | INTEGER    | 1 = price-per-m² heuristic, 0 = model         |
| feature_importances | TEXT     | JSON of top contributing features (explainability) |
| predicted_at      | TEXT       | UTC ISO-8601                                  |

UNIQUE(listing_id, model_version_id, run_id); index on (listing_id, predicted_at).

### detections

One row per (listing, run) that matches the enabled opportunity rules (spec FR-010, FR-011).

| column          | type       | notes                                     |
|-----------------|------------|-------------------------------------------|
| id              | INTEGER PK |                                           |
| listing_id      | INTEGER FK | → listings.id                             |
| run_id          | INTEGER FK | → runs.id                                 |
| baseline_id     | INTEGER FK | → baselines.id (snapshot used)            |
| prediction_id   | INTEGER FK | → predictions.id (nullable; value estimate used) |
| signals         | TEXT       | JSON array of signal objects (see below)  |
| score           | REAL       | aggregate score (0..1) for ordering       |
| status          | TEXT       | `active` \| `superseded` \| `resolved`    |
| first_seen_at   | TEXT       | UTC ISO-8601                              |
| last_seen_at    | TEXT       | UTC ISO-8601                              |
| created_at      | TEXT       | UTC ISO-8601                              |

UNIQUE(listing_id, run_id). New detection for a listing supersedes previous `active` detection (spec US3 acceptance 3).

Signal object shape:

```json
{
  "type": "undervaluation|yield|price_drop",
  "threshold": 0.10,
  "observed": 123456,
  "expected": 150000,
  "satisfied": true,
  "model_version_id": 4,
  "is_fallback": false
}
```

`expected` for the undervaluation signal is the predicted value (from `predictions`); `model_version_id` and `is_fallback` document the estimate source. Yield and price-drop signals may omit the model fields.

### notifications

One row per delivery attempt for a detection (spec FR-012, FR-013, FR-014).

| column           | type       | notes                                      |
|------------------|------------|--------------------------------------------|
| id               | INTEGER PK |                                            |
| detection_id     | INTEGER FK | → detections.id                           |
| run_id           | INTEGER FK | → runs.id                                  |
| channel          | TEXT       | `email` (extensible for future channels)   |
| recipient        | TEXT       | delivery address                           |
| status           | TEXT       | `pending` \| `sent` \| `failed`            |
| attempt_count    | INTEGER    | number of delivery attempts                |
| last_error       | TEXT       | nullable                                   |
| sent_at          | TEXT       | nullable; UTC ISO-8601                     |
| created_at       | TEXT       | UTC ISO-8601                               |

UNIQUE(detection_id, channel) — guarantees no duplicate alert for the same detection state (spec FR-013).

### settings

Simple key/value store for runtime config overrides (e.g., alert preferences persisted from CLI).

| column   | type      | notes          |
|----------|-----------|----------------|
| key      | TEXT PK   |                |
| value    | TEXT      | JSON-encoded   |

## Relationships

- runs 1—N pages, observations, price_history, detections, notifications, model_versions, predictions
- listings 1—N observations, price_history, detections, predictions
- zones 1—N baselines, listings (via denormalized region/barrio on listings)
- baselines 1—N detections
- model_versions 1—N predictions
- predictions 1—N detections (value estimate referenced by the undervaluation signal)

## Config-derived thresholds

Defaults (overridable via environment/`.env`):
- `MIN_OBSERVATIONS_PER_ZONE = 5` — below this, `baselines.is_sufficient = 0`
- `UNDERVALUATION_THRESHOLD = 0.10`
- `YIELD_THRESHOLD = 0.06`
- `PRICE_DROP_THRESHOLD = 0.05`
- `PRICE_DROP_LOOKBACK_DAYS = 30`
- `COLLECT_DELAY_SECONDS = 2.0`
- `MAX_PAGES_PER_SEARCH = 400`
- `ML_MIN_TRAIN_SAMPLES = 200` — below this, training is skipped and `predictions.is_fallback = 1`
- `LLM_TIMEOUT_SECONDS = 30` — LLM calls fail-open after this
- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` — when unset, LLM features are skipped (env only, never stored in DB)
