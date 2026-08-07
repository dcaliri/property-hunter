# Property Hunter

A single-user real-estate opportunity scanner for the Buenos Aires market.
It periodically collects listings from **inmoup.com.ar** (Argentina), stores them
with provenance, computes per-zone market baselines, trains a machine-learning
valuation model, flags potentially undervalued / high-yield / price-dropped
properties, and emails you a digest of new opportunities — optionally enriched
by an LLM.

This is a personal investment-research tool. Collection is throttled and polite,
and the pipeline is fully local: your SQLite database and your data never leave
your machine unless you configure an SMTP/LLM endpoint.

---

## Pipeline at a glance

```
collect  ──▶ analyze  ──▶ train ──▶ predict ──▶ detect ──▶ notify
  |          |             |           |            |          |
 scrape   zone medians   ML model   value est.  rule fires   email digest
 listings  (price, rent,  (gradient   (or zone    (3 signals)  (SMTP, retries,
           price/m²)     boosting)    median      supersede    LLM narrative)
                                    fallback)     old rows
```

Each stage is an independent command and also runs together via `run-all`
(sharing a single `runs` row). `scheduler` runs the whole pipeline on a daily
cron inside the container.

**Status:** v1 complete — all implementation tasks done, 81 tests passing
(see [Specs](#specs-and-design-documents)).

---

## Requirements

- **Python 3.12** and [uv](https://docs.astral.sh/uv/) (or `pip`)
- **Docker** only if you want the containerized scheduler (recommended)
- SMTP credentials only if you want email alerts (the rest of the pipeline runs
  without them)
- An OpenAI-compatible endpoint only if you want LLM enrichment (fully optional)

---

## Local setup

```sh
git clone <this-repo>
cd property-hunter

cp .env.example .env        # edit values (scope, SMTP, LLM, etc.)
uv sync --extra dev         # or: pip install -e ".[dev]"

uv run property_hunter init-db   # create the SQLite schema (also auto-run by every command)
```

Sanity check without hitting the network (uses saved fixture pages):

```sh
uv run property_hunter run-all --offline-fixtures
```

The default scope is `departamentos-en-venta-en-capital-federal` (sales in CABA).
Set `SCOPE_OPERATION=renta` or adjust `SCOPE_*` in `.env` for rentals / other
regions.

### No-network dev mode

`--offline-fixtures` reads saved pages from `tests/fixtures/` instead of the
live site. It is available on `collect` and `run-all` and is for local
development only — fixtures are not shipped in the Docker image.

---

## Running with Docker (your own containers)

The image is self-contained: Python 3.12-slim + the package, entrypoint, and a
volume for the database. The default container command runs the daily scheduler;
run any CLI stage with `docker compose run`.

### 1. Prepare configuration

```sh
cp .env.example .env
# edit .env: at minimum pick your scope; add SMTP_* + ALERT_EMAIL for alerts,
# and LLM_BASE_URL/LLM_MODEL/LLM_API_KEY for LLM enrichment.
```

`.env` is consumed via `env_file:` and is git-ignored. `DB_PATH` in the image
defaults to `/app/data/property_hunter.db` (overridden by the Dockerfile), which
is the mounted volume — your data survives container rebuilds.

### 2. Build and start the scheduler

```sh
docker compose up -d --build
docker compose logs -f app     # watch the daily run at SCHEDULE_DAILY_HOUR:MINUTE UTC
```

### 3. Run stages manually

```sh
# one-off full pipeline pass
docker compose run --rm app run-all

# individual stages
docker compose run --rm app collect
docker compose run --rm app analyze
docker compose run --rm app train --min-train-samples 200
docker compose run --rm app predict
docker compose run --rm app detect
docker compose run --rm app notify
```

### 4. Without compose

```sh
docker build -t property-hunter:latest .
mkdir -p ./data

# scheduled mode (default)
docker run -d --name property-hunter \
  -e DB_PATH=/app/data/property_hunter.db \
  -v "$(pwd)/data:/app/data" \
  --env-file .env \
  property-hunter:latest scheduler

# one-off stage
docker run --rm -v "$(pwd)/data:/app/data" --env-file .env \
  property-hunter:latest run-all
```

The entrypoint maps `scheduler` to `python -m property_hunter scheduler` and
passes everything else straight to the CLI. The `data/` bind-mount is where the
SQLite DB and gzipped page snapshots live; back it up or point `DB_PATH`
elsewhere as you wish.

---

## Deploy to AWS (cloud lifecycle)

A CLI-only lifecycle for running the pipeline in the cloud, provisioned with
Infrastructure-as-Code. One command provisions + deploys (scheduler + dashboard
+ persistent SQLite on a retained EBS volume), one command deprovisions, and
weekly cycles keep 100% of your data. Full validation guide:
[specs/002-cloud-provision-deploy/quickstart.md](specs/002-cloud-provision-deploy/quickstart.md).

**Design in brief** (see [research.md](specs/002-cloud-provision-deploy/research.md)):

- One ARM EC2 instance (`t4g.small` under the free trial, else `t4g.micro`)
  running the app image via docker compose. No SSH keys, no public dashboard:
  the UI binds `127.0.0.1:9000` and is reached through an SSM port-forward.
- App image is built locally, pushed to ECR, and pulled on the instance — deploys
  are pinned to a git ref, so `deploy.sh --ref <ref>` rolls back without
  re-provisioning.
- SQLite lives on a retained `gp3` EBS volume (tagged `property-hunter-data`)
  that `down` never deletes; it detaches and reattaches each cycle.
- Terraform state lives in a versioned S3 bucket; secrets live in SSM Parameter
  Store (`/property-hunter/*`, `SecureString`) — never in the repo.

### Prerequisites

- AWS account with billing + IAM, AWS CLI v2, and the
  [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).
- OpenTofu **or** Terraform ≥ 1.7, Docker, git, and Python 3.12 (for JSON output).
- Budget: **USD 5.25/mo** at the 20 GiB default during the t4g free-trial window
  (20 GiB EBS $1.60 + public IPv4 ~$3.65 for outbound egress; see the cost note
  below). Use `bootstrap.sh --volume-size 10` to land at **USD 4.45/mo** and stay
  under the USD 5 budget.

### One-time setup (per account)

```sh
# Static validation of the IaC (no AWS resources created):
./scripts/cloud/check.sh

# Creates the retained S3 state bucket + EBS volume + ECR repo (never removed by down):
./scripts/cloud/bootstrap.sh --bucket <globally-unique-name> --az us-east-1a --volume-size 20

# Push your local .env (git-ignored) into SSM Parameter Store:
./scripts/cloud/secrets.sh push
```

### Weekly lifecycle

```sh
./scripts/cloud/up.sh --bucket <name> --auto-approve   # provision + deploy
./scripts/cloud/dashboard.sh                           # SSM tunnel → http://localhost:9000
./scripts/cloud/status.sh --json                       # inventory + estimated cost
./scripts/cloud/deploy.sh --ref <old-tag>              # mid-cycle rollback (no re-provision)
./scripts/cloud/down.sh --bucket <name> --yes          # deprovision; data is kept
```

`down.sh` leaves the bucket, volume, and ECR repo intact; the next `up.sh`
reattaches the same volume, so collected listings/runs are still there. Add
`--wipe-data` to `down.sh` (double-confirmed, never implicit) for a truly fresh
start. Everything is idempotent — re-running `up.sh` while up or `down.sh` while
down is a clean no-op.

### Budget note

`status.sh` reports the estimated monthly cost against the **USD 5/mo** budget.
Compute (`t4g.small`) is free through the trial (until 2026-12-31); the EBS
volume is always billed (~$0.08/GiB-mo); a public IPv4 is needed for outbound
SSM/ECR/app traffic and bills ~$3.65/mo while the instance runs (it may be
covered by your account's public-IPv4 free allowance — verify in the console).
At the 20 GiB default the estimate is ~$5.25/mo (marginally over budget); a
10 GiB volume (0.80/mo) brings it to ~$4.45/mo. Nothing is exposed: the security
group has **no inbound rules** and the dashboard binds to `127.0.0.1`.
Part-time weekly cycles (up Mon–Thu, down Fri) cost less.

### CLI reference (cloud)

| Command | What it does |
| --- | --- |
| `scripts/cloud/check.sh` | Static validation: `terraform fmt -check`, `validate`, `sh -n` |
| `scripts/cloud/bootstrap.sh` | One-time retained S3 bucket + EBS volume + ECR repo |
| `scripts/cloud/secrets.sh <push\|pull\|list>` | Manage SSM `/property-hunter/*` secrets |
| `scripts/cloud/up.sh` | Provision + deploy (build image, apply, SSM deploy, health check) |
| `scripts/cloud/deploy.sh` | Redeploy/rollback at a git ref via SSM |
| `scripts/cloud/dashboard.sh` | Open the SSM port-forward tunnel to the dashboard |
| `scripts/cloud/status.sh` | Inventory + estimated cost (`--json` per the status schema) |
| `scripts/cloud/down.sh` | Deprovision; retained resources kept (`--wipe-data` to reset) |

`up.sh`/`down.sh`/`status.sh` support `--json`; output validates against
[`contracts/status-output-v1.schema.json`](specs/002-cloud-provision-deploy/contracts/status-output-v1.schema.json).

---

## CLI reference

| Command | Flags | What it does |
| --- | --- | --- |
| `init-db` | — | Create/upgrade the SQLite schema (idempotent; also run automatically by every command) |
| `run-all` | `--offline-fixtures` | Full pipeline in one pass: collect → analyze → train/predict → detect → notify, shared run id, per-stage status; `partial` status if a stage fails but later stages still run |
| `collect` | `--offline-fixtures`, `--max-pages N`, `--delay SECONDS`, `--scope TYPE-en-OPERATION-en-REGION` | Scan inmoup for the configured scope, store listings with provenance, dedupe, record price changes, delist missing items |
| `analyze` | — | Recompute per-zone baselines (median price, rent, price-per-m², observation count, window) |
| `train` | `--min-train-samples N` | Train the ML valuation model and record a `model_versions` row with R²/MAE |
| `predict` | — | Value-estimate every active sale listing (model-backed, or explicit zone-median fallback) |
| `detect` | — | Evaluate rules, write `detections` with signal JSON, supersede prior detections |
| `notify` | — | Email one digest per run covering pending detections; retries with backoff; one `notifications` row per detection |
| `scheduler` | — | Block and run `run-all` once daily (APScheduler cron, UTC) |
| `ui` | `--host 127.0.0.1`, `--port 8000` | Serve the local read-only web dashboard (stdlib only) |

Example: a custom scope on `collect`:

```sh
uv run property_hunter collect --scope casas-en-venta-en-capital-federal
```

### Dashboard

`uv run property_hunter ui` serves a zero-dependency read-only dashboard of
the database — listings (filter/sort/search, ask vs estimated value, barrio,
coordinates), detections (signals, score, notification status), baselines,
runs, model versions, and notifications. It binds to `127.0.0.1` by default
and queries the SQLite file directly (opened read-only); the page auto-loads
and has a manual refresh button. Run it in a separate terminal while the
scheduler container runs.

---

## Configuration reference

Configuration comes from environment variables (or a `.env` file via
python-dotenv). Copy `.env.example` and edit. No secrets have defaults.

| Variable | Default | Description |
| --- | --- | --- |
| **Scope** | | |
| `SCOPE_OPERATION` | `venta` | `venta` (sales) or `renta` (rentals) |
| `SCOPE_TYPE` | `departamentos` | Property type slug, e.g. `departamentos`, `casas` |
| `SCOPE_REGION` | `capital-federal` | Region slug, e.g. `capital-federal` |
| `MAX_PAGES_PER_SEARCH` | `400` | Upper bound on pages fetched per run (completion normally stops much earlier — see [Known behaviors](#known-behaviors)) |
| **Politeness** | | |
| `COLLECT_DELAY_SECONDS` | `2.0` | Delay between page fetches. Do not lower in production paths |
| `COLLECT_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `COLLECT_MAX_RETRIES` | `3` | Retries with backoff on 429/5xx/network errors |
| `USER_AGENT` | `property-hunter/0.1 (…)` | Identifiable user-agent string |
| **Database** | | |
| `DB_PATH` | `data/property_hunter.db` | SQLite path (relative to repo root locally; `/app/data/…` in the image) |
| `LOG_LEVEL` | `INFO` | Python logging level |
| **Baselines** | | |
| `MIN_OBSERVATIONS_PER_ZONE` | `5` | Minimum observations for a zone baseline to be "sufficient" |
| `BASELINE_WINDOW_DAYS` | `90` | Observation window for baseline computation |
| **Detection rules** | | |
| `UNDERVALUATION_THRESHOLD` | `0.10` | Flag when asking price is ≥10% below the model value estimate |
| `YIELD_THRESHOLD` | `0.06` | Flag when zone rent/m² × 12 ÷ asking price ≥ 6% |
| `PRICE_DROP_THRESHOLD` | `0.05` | Flag on a ≥5% price reduction |
| `PRICE_DROP_LOOKBACK_DAYS` | `30` | Window for the price-drop signal |
| `UNDERVALUATION_ENABLED` | `true` | Rule switch |
| `YIELD_ENABLED` | `true` | Rule switch |
| `PRICE_DROP_ENABLED` | `true` | Rule switch |
| **ML model** | | |
| `ML_MIN_TRAIN_SAMPLES` | `200` | Below this, training is skipped and prediction falls back to zone medians |
| `ML_TEST_SPLIT` | `0.2` | Held-out test fraction for R²/MAE reporting |
| **SMTP (notifications)** | | |
| `SMTP_HOST` | — | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` | Port (STARTTLS by default) |
| `SMTP_TLS` | `true` | Use STARTTLS |
| `SMTP_USER` | — | Auth user (empty = no login) |
| `SMTP_PASSWORD` | — | Auth password (never commit) |
| `SMTP_FROM` | — | From address |
| `ALERT_EMAIL` | — | Digest recipient |
| `NOTIFY_MAX_ATTEMPTS` | `3` | Delivery retry budget |
| `NOTIFY_RETRY_BACKOFF_BASE_SECONDS` | `1.0` | Exponential-backoff base between attempts |
| **LLM (optional)** | | |
| `LLM_BASE_URL` | — | OpenAI-compatible chat endpoint |
| `LLM_MODEL` | — | Model name |
| `LLM_API_KEY` | — | API key (never commit) |
| `LLM_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| **Scheduling** | | |
| `SCHEDULE_DAILY_HOUR` | `9` | Hour of the daily run (UTC) |
| `SCHEDULE_DAILY_MINUTE` | `0` | Minute of the daily run |

**LLM gating:** LLM features activate only when `LLM_BASE_URL`, `LLM_MODEL`,
**and** `LLM_API_KEY` are all set. Otherwise enrichment and the digest narrative
are skipped gracefully (logged as `llm.skipped`) and the digest renders fully
without them. LLM failures never break the pipeline (fail-open).

---

## How detection works

Every active sale listing is scored against three independent signals; each has
a configurable threshold and an on/off switch:

| Signal | Fires when | Source of comparison |
| --- | --- | --- |
| **Undervaluation** | asking price ≤ (1 − threshold) × market value | ML value estimate (records `model_version_id` + `is_fallback`) |
| **Yield** | zone rent/m² × 12 ÷ asking price ≥ threshold | zone rental baseline |
| **Price drop** | price dropped ≥ threshold within the lookback window | stored price history |

A `detections` row stores the signal JSON with the exact observed/expected
numbers and thresholds, plus `score` (signals fired / 3) — the reasoning is
auditable. Re-running detection supersedes the previous row for a listing
(history preserved). Listings in zones flagged insufficient data or with no
baseline are skipped rather than mis-scored.

---

## Data model & storage

All data lives in one SQLite database (WAL mode). Main tables:

| Table | Purpose |
| --- | --- |
| `runs` | One row per pipeline pass (trigger, started/finished, status `ok`/`partial`/`failed`) |
| `pages` | Fetched pages with URL, status, timestamp, and raw gzipped HTML snapshot (provenance) |
| `listings` | Canonical listings (identity = inmoup `inmuebles/{id}`), address, price, area, attributes, zone (`barrio`/`region`), exact map-marker `lat`/`lng`, `is_active` |
| `observations` | Per-run price observations |
| `price_history` | Append-only price changes (old, new, currency, observed_at) |
| `zones` | Region + barrio combination |
| `baselines` | Per-zone per-operation medians (price, rent, price/m²), counts, window, `is_sufficient`; history preserved across runs |
| `model_versions` | Trained model artifacts + measured R²/MAE on the holdout split |
| `predictions` | Value estimate per active listing, referencing the model version; `is_fallback=1` when zone-median fallback was used |
| `detections` | Opportunity signals (JSON) per listing per run, supersede-aware |
| `notifications` | Per-detection delivery rows (dedupe + `delivered`/`failed` status) |

Raw page snapshots are stored gzipped so any record can be traced back to the
exact bytes it was extracted from.

---

## Politeness & compliance

- Collection is throttled (2s default delay), uses an identifiable user-agent,
  and retries with backoff only on 429/5xx/transient errors. Keep the delay at
  or above the default in production paths.
- inmoup.com.ar terms allow **personal use only** — keep request volume low and
  respectful; this tool is for personal investment research.
- No secrets are committed; `.env` is git-ignored.

---

## Testing

```sh
uv run pytest tests -q        # 62 tests, ~12s
```

The suite follows the project constitution (test-first). It includes:

- **unit** — extraction, normalization, DB repository, baselines, features,
  ML training/prediction/fallback, detection rules, notify retries, LLM
  fail-open, and a 100k-observation baseline perf test
- **integration** — full pipeline over fixture pages (collect → analyze →
  train → detect → notify)
- **contract** — pipeline records validated against
  `specs/001-opportunity-hunter/contracts/` JSON schemas

---

## Project layout

```
src/property_hunter/
  cli.py, cli_runner.py     CLI entrypoints and per-command wiring
  pipeline.py               run_collect + run_all orchestrator
  scheduler.py              daily APScheduler entrypoint (Docker default)
  config.py                 env-driven settings (all defaults above)
  models.py                 pydantic records
  db.py                     SQLite repository (DDL, upserts, versioned rows)
  normalize.py              canonicalization, identity, zone assignment
  ingest/                   polite HTTP client, inmoup fetchers, RSC + JSON-LD extract
  analyze.py                per-zone baselines
  ml/                       features, HistGradientBoostingRegressor, predict + fallback
  detect.py                 opportunity rules
  notify/                   digest builder + SMTP transport w/ retries
  llm/                      optional enrichment tags + Spanish digest narrative
tests/                      unit/ integration/ contract/ fixtures/
specs/001-opportunity-hunter/  design documents (see below)
```

---

## Known behaviors

- **Pagination:** inmoup re-embeds the first page of listings in both its
  JSON-LD and its RSC payload on every page, so p1/p2 overlap. A run therefore
  stops at the first page that yields no *new* listing ids, which typically
  means a single run collects ~24 unique listings even though
  `MAX_PAGES_PER_SEARCH` is high.
- **Location/barrio:** a listing's `barrio` is its own `localidad` as
  published by inmoup (the same geocoding that places the map marker); the
  exact map-marker coordinates are stored on each listing (`lat`/`lng`) for
  verification. The real-estate agency's office address is never used — it
  differs from the property's location (an early run mis-barrio'd listings to
  the agency's neighborhood).
- **Unknown barrios:** listings whose barrio is not recognized are bucketed
  into a fallback zone by region instead of being dropped.
- **Notifications without SMTP:** `notify` (and `run-all`) will attempt
  delivery, exhaust the retry budget, and record `failed` rows — it fails
  gracefully but visibly. Set `SMTP_HOST`/`SMTP_USER`/`ALERT_EMAIL` to get
  actual mail.

---

## Specs and design documents

The feature was built with spec-driven development; the design documents in
`specs/001-opportunity-hunter/` are the source of truth:

- `spec.md` — requirements, user stories, functional requirements (FR-001…),
  quality scenarios (SC-001…)
- `plan.md`, `tasks.md` — planning and implementation checklist
- `data-model.md` — schema and entity design
- `contracts/` — JSON schemas for listing/detection/notification records
- `quickstart.md` — scenario-by-scenario validation guide (A–F)
