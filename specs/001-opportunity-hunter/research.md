# Research Notes: Property Opportunity Hunter

Phase 0 output of `/speckit.plan`. Each section records a Decision, its Rationale, and Alternatives considered.

## 1. Source data acquisition: inmoup.com.ar

**Decision**: Fetch the site's server-rendered pages over HTTP with an identifiable user-agent and a politeness delay. List pages (`/{operation}-{type}-en-{region}?pagina=N`) are parsed primarily from the Next.js **RSC payload** (the `self.__next_f.push([1,"..."])` string chunks) — specifically its `"initialItems"` array, which carries per-listing `localidad`, `provincia`, exact `coordenadas` (the map-marker lat/lng), `direccion`, `precio` (USD int) + `dolar` flag, `servicios` (Dormitorios, Baños, Superficie Cubierta/Total m²), `inmobiliaria`, and `slug`. Each RSC item is enriched with the schema.org **JSON-LD** blocks on the same page (description, `datePosted`, canonical URL matched by listing id). Detail pages (`/{agency}/inmuebles/{id}/ficha/{slug}`) expose a full `Accommodation`/`RealEstateListing` JSON-LD graph.

**Rationale**:
- Confirmed by direct inspection (2026-08-06, list fixtures p1/p2, 24 items each, ids matching JSON-LD `/{id}/`): RSC `initialItems` provides the property's own `localidad` and exact map-marker coordinates — the ground truth for zone assignment — plus price/attributes, with no per-listing detail fetch.
- The list-page JSON-LD `ItemList` (kept as `parse_ld_list_page`) includes `url` (with the listing id), `offers.price`, `offers.priceCurrency`, `additionalProperty` (Dormitorios, Baños, Superficie m²), `provider.name`, and the barrio embedded in the item `name`.
- Detail-page JSON-LD adds `address.streetAddress`, `address.addressLocality` (barrio), `addressRegion`, `geo`, `datePosted`, `brand`/`provider` (with public contact info), `offers`, and richer `additionalProperty` (Superficie Total, Antigüedad, Estado, Ambientes).
- JSON-LD/RSC are far more stable and simpler to parse than the rendered HTML cards and do not require a browser runtime.
- The list page alone provides everything needed for pricing, attributes, and zone assignment, so per-listing detail fetches are not required for the daily pass.

**Alternatives considered**:
- HTML/CSS card scraping (selectors brittle, page changes break extraction).
- Headless browser (Playwright): heavy, unnecessary, and increases request footprint.
- An official API: none published; robots.txt returned 502 (not served at time of check) — must be re-checked at deploy time and honored. Note (2026-08-07): the site's own Next.js client API (`POST /server/inmuebles/buscar`, discovered in the JS bundle) is used for pagination per §2, with the politeness delay applied to every request.

**Politeness policy** (constitution II): default delay ≥2s between requests, retries with backoff on 429/5xx, bounded pages per search (default: configurable cap; daily pass for CABA stays well within polite volume), identifiable user-agent string, and no concurrent bursts. Terms of service prohibit commercial exploitation and disproportionate load; collection is personal-use only.

## 2. Pagination & completion detection

**Bug found 2026-08-07**: the server-rendered `?pagina=N` pages **do not paginate** — every `?pagina=2,3,...` re-renders the same first 24 items (RSC `initialItems`, JSON-LD `ItemList`, and the rendered cards are identical), so an SSR-only collector stops after ~23 unique listings regardless of the site's reported total (e.g. 3.831 casas / 3.233 departamentos en venta en Mendoza → 23 collected).

**Decision**: Collect page 1 via the SSR payload as before (it is the cheapest way to obtain the exact per-listing cards and the embedded search state), then paginate the remainder through the site's own **JSON API** — `POST https://inmoup.com.ar/server/inmuebles/buscar` with body `{"filtros": {…}, "page": N, "limit": 24}` — which honors `page` (zero overlap between pages) and reports the total count in `total`. The response items carry the exact same fields as the RSC `initialItems` cards plus `descripcion`/`fechaPublicacion`, so they feed the same normalization path.

**Rationale**:
- The search-filter state is embedded in the SSR page as `"filters":{"grupo":2,"condiciones":1,"provincias":[1]}` inside the RSC payload (`parse_rsc_filters`). That object is exactly the `filtros` body the JSON API accepts, so region/operation/type resolution needs **no hardcoding** — any configured slug's page yields its own filters.
- `grupo` maps to property type (1=casas, 2=departamentos, 3=lotes-y-terrenos, 4=locales, 5=cocheras, 6=bodegas, 7=industrias, 8=edificios); `condiciones` maps to operation (1=venta; items carry `condicion: "Venta"`). Prices may be ARS or USD — the `dolar` flag (1=USD, 0=ARS) disambiguates.
- The API requires `Origin: https://inmoup.com.ar` + `Referer: https://inmoup.com.ar/` headers (403 otherwise); cookies are not required. `limit` is capped at 24 by the server even if larger values are sent.
- Completion: stop when the number of seen ids reaches the reported `total`, or when a page yields no new items (safety net, matching the earlier heuristic). 3045 departamentos ÷ 24 ≈ 127 requests ≈ 4–5 min at the 2 s politeness delay — well within the polite volume for a daily pass.
- If the API is unavailable, the collector degrades to the SSR-only loop (single page of items) and logs the fallback.

**Alternative considered**: the `POST /server/buscar/filtros` endpoint (`{"term":"mendoza"}` → `{"filtros":{"provincias":[21]}}`) for slug→filters resolution — rejected: it is unreliable for some slugs ("capital federal" returns the default `{"condiciones":1,"grupo":1}` instead of `provincias:[1]`), so reading the filters straight off each page is more robust.

## 3. Stable identity & dedup

**Decision**: The canonical listing URL (`https://inmoup.com.ar/{agency_id}-{agency_slug}/inmuebles/{id}/ficha/{slug}` — note the agency's **id-prefixed** slug, e.g. `17636-gold-group-argentina`) — specifically the `{id}` segment — is the stable source identity. Persist the full URL and derive `listing_id` from the URL.

**Rationale**: The detail-page JSON-LD exposes the canonical URL; slugs may change but the `inmuebles/{id}` path is the immutable key. A bare agency slug (`gold-group-argentina`) 404s — the site serves detail pages only under the id-prefixed slug — so URLs built from API items prefix `inmobiliaria.id` onto `inmobiliaria.slug` (`_card_url`). This satisfies constitution III (stable identity, no duplicates across runs).

**Alternatives considered**: Hashing street address + attributes (fragile; addresses are often partial like "av cordoba al 4500").

**Bug found 2026-08-07**: the first API-collected run stored detail URLs with the bare agency slug (the API's `inmobiliaria.slug` is unprefixed), so detail links from the dashboard 404'd. Fixed in `_card_url` and backfilled by re-collecting (`upsert_listing` refreshes `source_url`).

## 4. Currency & price normalization

**Decision**: Parse `offers.price` and `offers.priceCurrency`; store price in integer minor units of USD (`price_cents`) plus the original currency code and raw string.

**Rationale**: The site quotes US dollars; storing cents avoids float drift and matches the spec assumption (single currency in v1). Keeping the original string preserves provenance for debugging.

## 5. Storage: SQLite

**Decision**: Single SQLite database (WAL mode, foreign keys on) holding listings, observations, price history, zones, baselines, detections, notifications, run log, and gzipped raw page snapshots.

**Rationale**: Single-user, job-oriented workload; zero infrastructure; crash-safe via transactions; the whole DB is one file for backups. WAL enables the reader (baseline queries) to run while a writer (ingest) is active. Snapshot size is controlled by storing raw HTML gzipped.

**Alternatives considered**: PostgreSQL (operational overhead unjustified for single user in v1 — noted in research for a future multi-user/commercial version), parquet/delta files (adds complexity, no query layer needed).

## 6. Zone definition

**Decision**: Zone = barrio (neighborhood) within `addressRegion` (province/locality), e.g., "Almagro, Capital Federal". Barrio comes from the property's own `localidad` in the RSC `initialItems` payload (the same geocoding that places the listing's map marker); the exact map-marker coordinates (`coordenadas`) are always stored as `lat`/`lng` on the listing for verification and future audits. The agency's address is **never** used as the barrio.

**Rationale**: The source publishes barrio as its native geographic unit; spec default is neighborhood-level granularity. This yields statistically meaningful baselines in CABA (many listings per barrio).

**Field bug fixed 2026-08-07**: JSON-LD `provider.address.addressLocality` is the *real-estate agency's office* (e.g. Coldwell Banker Lion Team → Villa Urquiza), not the property's location. A first live run mis-barrio'd listings (8367 Villa Urquiza → actually Almagro, 8366 Villa Urquiza → actually Mataderos, 396 Belgrano → actually Flores). Verified against the RSC `coordenadas`/`localidad`; corrected by parsing `localidad` + storing `coordenadas` and dropping the agency-address fallback.

**Alternatives considered**: (a) reverse-geocoding the stored coordinates via an external API — rejected: extra network dependency, rate limits, and inmoup already publishes the authoritative neighborhood; (b) shipping a CABA GeoJSON polygon for point-in-polygon — rejected: stale boundaries, larger footprint for no accuracy gain over the source's own `localidad`.

## 7. Baseline statistics

**Decision**: Per zone × operation (sale|rent) × property type, compute over active observations within the current time window: median price, median rent, median price-per-m², count, window start/end. Use simple median (numpy-free pure Python or small helper) with an explicit minimum-observation threshold (default 5; configurable).

**Rationale**: Medians are robust to the outlier-prone listings common in real-estate data. The threshold avoids the "single listing zone" trap (spec FR-009). Baselines are stored with their window as immutable rows (history preserved per spec).

## 8. Opportunity rules (configurable combo)

**Decision**: Implement three independently enableable rules with thresholds from config:
1. **Undervaluation**: `(value_estimate - listing_price) / value_estimate ≥ threshold` (default 0.10), where `value_estimate` is the ML model's predicted market value for the property (per decision 13), falling back to `listing_price_per_m2 / median_price_per_m2_zone` against the zone sales baseline when no model estimate is available.
2. **Rental yield**: `(annualized zone median rent per m² × listing covered m²) / listing price ≥ threshold` (default 0.06).
3. **Price drop**: most recent price history entry older than 0 days and within the lookback window (default 30) with a relative drop ≥ threshold (default 0.05).
Each detection records the exact observed/expected numbers, the model estimate and version when used (explainability per FR-011, FR-021).

**Rationale**: These map 1:1 to the spec's clarified "configurable combination" and each is testable in isolation. The undervaluation rule now scores against a per-property model estimate (FR-021) instead of a single zone average, which accounts for the property's own attributes.

## 13. ML valuation model

**Decision**: Train a **HistGradientBoostingRegressor** (scikit-learn) on the active sale listings: target = asking price; features = one-hot barrio, property type, bedrooms, bathrooms, covered m², total m², age. Predict log-price (train on log1p target, expm1 the prediction) to keep residuals homoscedastic on a skewed price distribution. Persist each trained model as a versioned row (blob + version id + training window + dataset size + R² and MAE on a held-out split), retrain after each successful collection pass that adds new sale data, and recompute predictions for all active listings. If the training set is below a minimum size (default 200 active sales) or training fails, record a fallback and use the zone price-per-m² heuristic estimate instead. For explainability, record each prediction's top contributing features (feature importances from the trained tree ensemble for that version).

**Rationale**: Gradient boosting handles mixed categorical/numeric features and missing values natively, and outperforms linear/ridge models on small tabular real-estate data without hyperparameter tuning (defaults are strong). Model versioning and quality metrics satisfy FR-018/19 and constitution I; versioned predictions make stale estimates identifiable. Fallback satisfies FR-020/21 (pipeline never blocked). scikit-learn's `HistGradientBoostingRegressor` is pure-numpy, fast to train on tens of thousands of rows, and adds no heavy dependency beyond scikit-learn.

**Alternatives considered**: Linear/ridge regression (poor fit on skewed, non-linear data), XGBoost/LightGBM (external C++ deps, no benefit at this scale), a deep network (overkill and slow to train in a container), LLM-based valuation (unreliable, non-deterministic, and expensive per-listing).

## 14. LLM description enrichment & digest narrative

**Decision**: Implement an optional LLM stage over an **OpenAI-compatible chat-completions endpoint**, configured via env (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). When unset, the stage is skipped and the run logs `llm.skipped`. Two capabilities: (a) **enrichment** — for each new listing description, one request returns a small JSON of amenity/condition tags (e.g., `["balcón", "cocina a nuevo", "estado: a refaccionar"]`) validated against a fixed tag vocabulary; (b) **narrative** — one request per notify pass summarizes the day's detected opportunities in plain Spanish for the digest opening. Requests use a short timeout (default 30s), fail-open (log + skip, never abort the run), and are budgeted to at most one enrichment request per new description per run. No API key is committed; enrichment and narrative never gate detections or notifications (templated digest renders without them).

**Rationale**: A real generative component (per user's ML/LLM requirement) that adds user-visible value without touching the deterministic core. The OpenAI-compatible wire protocol works with OpenAI, Groq, OpenRouter, Ollama, and local gateways, so no provider lock-in and no new SDK dependency (thin httpx client). Fail-open + budgeted calls keep it safe and cheap.

**Alternatives considered**: Bundling a local model (heavy in-container GPU/RAM, poor UX), scraping amenity data from the site (not reliably structured), making the LLM decide detections (rejected: non-deterministic, violates explainability).

## 9. Notification: email digest

**Decision**: SMTP delivery using stdlib `smtplib` + `email`, configured via env (host, port, TLS, credentials, from/to). One digest email per notify pass listing all newly detected opportunities (address, price, signals, baseline context, listing link). Retry with exponential backoff (default 3 attempts), record delivery status per notification row, and surface permanent failures in the run log/report.

**Rationale**: SMTP works with any provider (SES, Resend, Gmail app password, self-hosted) without adding a vendor dependency; retry/backoff and persisted status satisfy FR-014 and the observability section of the constitution.

**Alternatives considered**: Transactional email API (adds a dependency; SMTP suffices for single-user volume), webhook/Slack (future work; channel abstraction is part of the module design).

## 10. Scheduling & deployment

**Decision**: APScheduler in-process daily job inside a long-running container; manual passes via the same image through `docker compose run --rm app run-all`. Dockerfile from `python:3.12-slim`; env-driven config via `.env`; data volume for `data/`.

**Rationale**: One artifact (image) for both scheduled and manual execution; no external scheduler dependency in v1. Production-ready without a web service.

**Alternatives considered**: system cron + venv (works but less portable/reproducible), GitHub Actions cron (host is unavailable during the job and DB is not a clean fit), serverless functions (cold starts + no long-lived DB connection benefit for a daily job).

## 11. Observability

**Decision**: JSON-formatted structured logs (stdlib `logging` with a custom JSON `Formatter`), one log line per stage with run id, plus a `runs` table recording start/end, per-stage status, counts, and errors. CLI exit codes and a final run summary for cron/scheduler awareness.

**Rationale**: Satisfies constitution I (structured logs, metrics) with zero new dependencies.

## 12. Testing strategy

**Decision**: Fixture-based tests using real pages captured from inmoup.com.ar (list pages p1/p2 and a detail page, stored under `tests/fixtures/`). Unit tests cover extraction, normalization, identity/dedup, baseline math, feature engineering, model training/persistence/fallback, LLM client (stubbed transport: fail-open, tags, narrative), and each detection rule. Integration tests run a full pipeline against fixtures with a temp SQLite DB, including a small synthetic training set for the ML stage. Contract tests validate emitted records against JSON schemas in `contracts/`.

**Rationale**: Real fixtures exercise the actual JSON-LD shapes observed (constitution IV) and decouple tests from live network access (no CI flakiness, no load on the source).
