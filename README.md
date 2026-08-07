# Property Hunter

Find investment opportunities in Buenos Aires rental/housing listings:
collect listings from inmoup.com.ar, compute per-zone market baselines, train a
valuation model, detect undervalued/high-yield/price-dropped properties, and
email a digest of new opportunities — optionally enriched by an LLM.

Behavior is specified in `specs/001-opportunity-hunter/` (single source of
truth: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `tasks.md`,
`contracts/`).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev
cp .env.example .env   # fill in real values
uv run property_hunter init-db
```

## Usage

```sh
uv run property_hunter collect            # scrape the configured scope
uv run property_hunter analyze            # per-zone baselines
uv run property_hunter train              # train the valuation model (needs >= ML_MIN_TRAIN_SAMPLES)
uv run property_hunter predict            # value estimates for active listings
uv run property_hunter detect             # opportunity rules -> detections
uv run property_hunter notify             # email digest of new detections
uv run property_hunter run-all            # all stages in one pass (shared run)
uv run property_hunter scheduler          # daily cron (Docker default)
```

Offline development mode (no network):

```sh
uv run property_hunter collect --offline-fixtures
```

### Docker

```sh
docker compose up --build   # runs the scheduler daily
docker compose run --rm app collect   # one-off manual collect
```

## Configuration

See `.env.example` for every setting. Key groups:

| Group | Variables |
| --- | --- |
| Scope | `SCOPE_OPERATION`, `SCOPE_TYPE`, `SCOPE_REGION`, `MAX_PAGES_PER_SEARCH` |
| Politeness | `COLLECT_DELAY_SECONDS`, `COLLECT_MAX_RETRIES`, `USER_AGENT` |
| Baselines | `MIN_OBSERVATIONS_PER_ZONE`, `BASELINE_WINDOW_DAYS` |
| Rules | `UNDERVALUATION_THRESHOLD`, `YIELD_THRESHOLD`, `PRICE_DROP_THRESHOLD`, `*_ENABLED` |
| ML | `ML_MIN_TRAIN_SAMPLES`, `ML_TEST_SPLIT` |
| SMTP | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `ALERT_EMAIL`, `NOTIFY_*` |
| LLM (optional) | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS` |
| Scheduling | `SCHEDULE_DAILY_HOUR`, `SCHEDULE_DAILY_MINUTE` |

## Compliance & politeness

- Collection is throttled (2s default delay), uses an identifiable user-agent,
  and retries with backoff on 429/5xx. Do not reduce the delay or bypass these
  limits in production paths (constitution II).
- The site terms allow personal use only; keep usage low and respectful.
- Raw page snapshots are stored gzipped for provenance.

## Testing

```sh
uv run pytest tests -q
```

Tests follow the project constitution (test-first). The contract suite
validates pipeline records against `specs/001-opportunity-hunter/contracts/`.

## Architecture

- `src/property_hunter/ingest/` — polite HTTP client, inmoup fetchers, JSON-LD extraction
- `src/property_hunter/normalize.py` — canonicalization, identity, zone assignment
- `src/property_hunter/analyze.py` — zone baselines (median price/rent, price-per-m²)
- `src/property_hunter/ml/` — feature engineering, training, prediction + fallback
- `src/property_hunter/detect.py` — opportunity rules (undervaluation, yield, price drop)
- `src/property_hunter/notify/` — email digest + delivery retries
- `src/property_hunter/llm/` — optional enrichment + digest narrative (fail-open)
- `src/property_hunter/db.py` — SQLite repository (WAL, versioned rows, snapshots)
