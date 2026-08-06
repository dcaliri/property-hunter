# Quickstart: Property Opportunity Hunter

Validation guide for the v1 pipeline. Implementation details live in `tasks.md` and the code; this file proves the feature works end-to-end.

## Prerequisites

- Python 3.12, `uv` (or pip)
- Docker (for the containerized scheduler) — optional for local validation
- SMTP credentials for the notify stage (host/port/TLS/user/pass) — only needed when validating notifications; the rest of the pipeline works without it
- Optional: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (OpenAI-compatible endpoint) for description enrichment and the digest narrative; without them these features are skipped

## Local setup

```bash
cp .env.example .env          # then edit: SMTP_*, ALERT_EMAIL, scope
uv sync                       # or: pip install -e ".[dev]"
uv run property_hunter init-db
```

## Validation scenarios

### Scenario A — Collection & storage (User Story 1)

```bash
uv run property_hunter collect --scope departamentos-en-venta-en-capital-federal --max-pages 3 --delay 0.5
```

Expected: listings persisted in SQLite `data/property_hunter.db` (via fixture-safe offline mode first; see below), each with source, URL, price, barrio, region, beds/baths/area, and page provenance. Re-running the same scope produces **no duplicate** listings and records price history only when prices differ.

Offline variant (no network, uses `tests/fixtures/`):

```bash
uv run property_hunter collect --offline-fixtures
```

### Scenario B — Zone baselines & ML value estimates (User Story 2)

```bash
uv run property_hunter analyze
uv run property_hunter train      # optional: explicitly retrain the valuation model
uv run property_hunter predict    # optional: recompute per-listing value estimates
```

Expected: for every zone (region+barrio) with ≥5 observations, a `baselines` row per operation with median price, median rent, and median price-per-m² and `is_sufficient=1`; zones below threshold flagged `is_sufficient=0`. Training produces a `model_versions` row (current) with R²/MAE; prediction gives every active sale listing a `predictions` row referencing the model version, or an explicit `is_fallback=1` estimate when training data is insufficient. SQL sanity check:

```bash
sqlite3 data/property_hunter.db \
  "select b.barrio, bl.operation, bl.observation_count, bl.median_price_cents, bl.median_price_per_m2_cents
   from baselines bl join zones b on b.id=bl.zone_id order by bl.computed_at desc limit 10;"
sqlite3 data/property_hunter.db \
  "select count(*) as total, sum(is_fallback=0) as with_model, sum(is_fallback=1) as fallback
   from predictions where predicted_at=(select max(predicted_at) from predictions);"
```

### Scenario C — Detection (User Story 3)

```bash
uv run property_hunter detect
```

Expected: `detections` rows for active sale listings whose enabled signals fire; each row's `signals` JSON contains the exact observed/expected numbers and thresholds, and the undervaluation signal references the model value estimate (`model_version_id`/`is_fallback`). Re-running the same run state supersedes the previous detection for a listing instead of duplicating.

### Scenario D — Notification (User Story 4)

```bash
uv run property_hunter notify
```

Expected: one digest email listing newly detected opportunities (address, price, signals, baseline context, link), optionally opening with a plain-language LLM summary and per-opportunity amenity tags when configured; each detection has exactly one `notifications` row (no duplicates); failures are retried with backoff and recorded as `failed` after the retry budget. Without LLM config, the digest renders fully without the narrative/tags and the run logs `llm.skipped`.

### Scenario E — Full run (all stages)

```bash
uv run property_hunter run-all
```

Expected: one `runs` row with status `ok`, per-stage log lines with counts, and a final summary. Crash mid-run leaves the DB consistent (transactions); a manual re-run recovers to a consistent state. Scheduler cadence guarantees digests are sent within 10 minutes of the detection pass (SC-005).

### Scenario F — Containerized scheduler

```bash
docker compose up -d --build     # starts scheduler container (daily job)
docker compose run --rm app run-all   # manual pass
```

Expected: scheduler logs a daily run; manual runs reuse the same SQLite data volume.

## Not in scope for v1 validation

- Live full-country crawl (set scope explicitly; default CABA)
- Additional notification channels (channel abstraction is reserved)
- Web UI
