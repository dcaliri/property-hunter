# Local validation (Ollama) → production plan (OpenAI)

One-pager documenting (a) what we validated locally against Ollama, (b) how we
flip to OpenAI in production, (c) what it costs, and (d) which knobs we will
turn to reach our target metrics.

Related: [`ml-llm-setup.md`](./ml-llm-setup.md) (how the ML model + LLM hook
into `.env`), [`research.md`](./research.md) (earlier exploration).

---

## 1. What we tested locally with Ollama

Setup: `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=llama3.1:8b`,
`LLM_API_KEY=ollama` (Ollama ignores the key). 1,833 usable sale listings in
the DB. All CLI commands run via `uv run property-hunter …`.

### 1.1 Structured feature extraction (`enrich-features`)

- Extracts 8 fields per listing: `condition`, `floor`, `expensas`,
  `orientation`, and 6 amenities (`has_parking`, `has_pool`, `has_gym`,
  `has_terrace`, `has_balcony`, `has_security`).
- **Full pass completed: 1,849/1,849 listings, 0 failures**, in ~62 min
  serial (~2 s/listing) on a MacBook with `llama3.1:8b`. Each stage is
  idempotent (`llm_features_updated_at IS NULL` filter), so it can be resumed
  and parallelized.
- **Quality is strong** on: `a estrenar` vs `nuevo` vs `renovado` vs
  `buen_estado`; floor parsing ("tercer piso"→3, "planta baja"→0); `expensas`
  in ARS; compass orientation; amenities (parking, pool, gym, terrace,
  balcony, security).
- **Weakness found:** ~1 in 15 responses (~7%) came back unparseable and were
  stored as `unknown`. Result: 205/1,849 (11%) of enriched listings have
  `condition = unknown`.
  - **Fix implemented:** all JSON-producing stages now send
    `response_format={"type":"json_object"}` (JSON mode). Verified against
    live Ollama — it honors it and returns clean JSON, so unparseable
    responses should drop to ~0 for newly enriched rows.
- Resulting distribution: `buen_estado` 936, `a_estrenar` 396, `unknown` 205,
  `nuevo` 196, `renovado` 78, `regular` 31, `a_refaccionar` 7.

### 1.2 Valuation model A/B (`backtest`)

Temporal backtest: train on the oldest 80%, evaluate on the most recent 20%
(prevents lookahead). Metrics: MAPE (mean absolute % error), MdAPE (median),
R² (variance explained), and how often the model beats the zone-median
fallback.

| Configuration | MAPE | MdAPE | R² | Beats fallback |
|---|---|---|---|---|
| Baseline (no LLM features) | 40.2% | 27.9% | 0.374 | 46% of listings |
| **+ LLM features (`llama3.1:8b`)** | **27.4%** | **18.9%** | **0.511** | **63%** |

- The 8 LLM features cut MAPE by **12.8pp** and lift R² by **+0.14** — a
  material, reproducible gain on the honest temporal split.
- The base model (trained on old prices) goes stale in an inflationary market
  and *loses to the zone-median fallback* on the temporal split (46%); the LLM
  features carry cross-time signal (condition, amenities, age) that restores
  it to beating the fallback 63% of the time.
- Per-zone examples (without → with): Capital 45.0%→29.5%, Godoy Cruz
  36.6%→27.8%, Guaymallén 34.6%→19.6%. Gains are broad, not driven by one barrio.
- **The live production model is still the old one** (`model_id=1`, trained on
  1,833 listings, no LLM features, random-split R² 0.534). It keeps working
  during the switch (1,918 predictions, 69 fallbacks = 3.6%), so there is no
  outage risk — we only improve numbers once we retrain.

### 1.3 Caveats we proved out

- Old model + new feature column is **backward compatible** (vector slicing in
  `predict.py`): no crash, no silent fallback storm.
- `response_format` is accepted by Ollama's `/v1` compat layer (checked with a
  live call before enabling it for OpenAI).
- Test suite: 105 passed / 1 pre-existing env-dependent failure
  (`test_notify_llm_narrative`).

---

## 2. Production plan with OpenAI

The LLM client is provider-agnostic (`base_url` + `model` + `api_key`), so
switching is config-only. JSON mode is already on for the JSON stages.

### Steps

| # | Action | Command / detail |
|---|---|---|
| 1 | Point `.env` at OpenAI | `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL=gpt-4.1-mini`, `LLM_API_KEY=sk-…` |
| 2 | Smoke test | `property-hunter enrich-features --limit 3` → eyeball 3 extractions |
| 3 | Backfill features | `property-hunter enrich-features` (1,849 existing listings, ~$0.9, see §3). Re-enriching the 205 `unknown` listings to repair them via JSON mode is optional but cheap. |
| 4 | Retrain valuation model | `property-hunter train` → new `model_version` becomes current (this is the step that actually turns LLM features into better detections) |
| 5 | Verify A/B | `property-hunter backtest` (with features) vs `property-hunter backtest --no-llm-features` on the same cutoff → confirm ≥ baseline numbers from §1.2 |
| 6 | Repredict + detect | `property-hunter predict` then `property-hunter detect`; eyeball the new detection list |
| 7 | Go live | existing `scheduler`/`run-all` path picks it up automatically (collect → analyze → train/predict → detect → notify) |

Amenity tags (`llm_amenity_tags`) are content-only (digest wording) and are
currently 0 in the DB; they get filled naturally on notify passes — no backfill
needed for ML.

### Go/no-go

Ship it when step 5 reproduces a **≥ 5pp MAPE improvement** over baseline with
no barrio regressing more than ~1pp. If OpenAI's extraction quality differs
from Ollama's, the numbers in §1.2 are the floor to beat.

---

## 3. Costs (gpt-4.1 pricing, Aug 2026)

Pricing: `gpt-4.1-mini` $0.40/M input, $1.60/M output (`gpt-4.1-nano`
$0.10/$0.40, i.e. **~4× cheaper**). Batch API = 50% off. Rough tokens per
call: extraction ~700 in / ~130 out; tags ~400 in / ~60 out.

| Scope | Calls | Est. cost (mini) | Batch (50%) |
|---|---|---|---|
| Per listing, features only | 1 | ~$0.0005 | ~$0.0003 |
| Per listing, features + tags | 2 | ~$0.0008 | ~$0.0004 |
| **One-time backfill (1,849 listings)** | 1,849 | **~$0.90** | ~$0.45 |
| Ongoing 10 new listings/day | 300/mo | ~$0.25/mo | ~$0.12/mo |
| Ongoing 50 listings/day | 1,500/mo | ~$1.2/mo | ~$0.60/mo |
| Ongoing 100 listings/day | 3,000/mo | ~$2.4/mo | ~$1.20/mo |
| Digest narrative | 1/day | <$0.01/mo | — |

Realistic steady state (a market like Mendoza, tens of new listings/day) is
**under $1.50/month on mini**, or ~$0.40/month on **nano** if the backtest
delta holds. Every number can be halved by running backfills through the
Batch API (async, up to 24 h, fine for a nightly enrich).

---

## 4. Iterating the knobs to hit our targets

### Targets (on the temporal backtest)

| Metric | Now (llama, with features) | Target |
|---|---|---|
| MAPE | 27.4% | **≤ 22%** |
| MdAPE | 18.9% | **≤ 17%** |
| R² | 0.511 | **≥ 0.60** |
| `unknown` condition rate | 11% (legacy rows) | **≤ 2%** (new rows) |
| LLM-feature delta vs baseline | +12.8pp MAPE | hold ≥ 5pp |

**Why this matters for detections:** with MAPE ~27%, the 10% undervaluation
threshold sits *inside* the model's noise band, so many current detections are
noise. Shrinking MAPE is what turns "undervalued" signals into real ones.

### The knob inventory

| Knob | Where | Current | How to tune |
|---|---|---|---|
| LLM model | `.env LLM_MODEL` | `llama3.1:8b` | mini → nano → mini; judge by backtest delta vs cost |
| JSON mode | code, on for JSON stages | on | keeps unparseable ≈ 0 |
| Extraction prompt | `features_extract.py` | alias list + amenity vocab | add Mendoza phrasing ("usado", "muy buen estado", "quincho"); reduces `unknown`s |
| Feature set | `train.py` | 19 base (live) / +8 LLM | add/remove fields; watch `feature_importances` |
| Undervaluation threshold | `detect.py` | 0.10 | raise → fewer, higher-confidence signals; lower → more volume |
| Yield / price-drop thresholds | `detect.py` | fixed | tune for volume vs precision |
| `ML_MIN_TRAIN_SAMPLES` | `.env` | 200 | raise to ignore stale data; lower to retrain sooner |
| `ML_TEST_SPLIT` | `.env` | 0.2 | keep fixed for comparable backtests |
| Model hyperparams | `train.py` (hardcoded) | `max_iter=150`, `lr=0.08`, `min_samples_leaf=4` | make env-tunable only if a grid search is warranted |
| Retrain cadence | scheduler | daily | weekly is fine; backtest before promoting |

### The iteration loop (guardrail)

1. **Change one knob at a time** — anything else invalidates the comparison.
2. Run `property-hunter backtest` on a **fixed cutoff** (e.g. the
   2026-08-07 window) and compare against §1.2.
3. Promote the change only if MAPE/R² improve without a per-zone regression
   (> ~1pp in the worst barrio).
4. Retrain → `predict` → `detect`; sanity-check the detection list by hand.
5. **Log the result** in §5 so we can tell what actually moved the needle.

Suggested first experiments (cheapest wins):
1. OpenAI `gpt-4.1-mini` re-run of step 5 in §2 → validates parity.
2. `gpt-4.1-nano` vs `mini` A/B → decide if we keep 4× cheaper.
3. Enrichment prompt v2 (more aliases + explicit "if unsure, use
   `buen_estado`") → attack the `unknown`/condition accuracy.
4. Threshold sweep (0.08 / 0.10 / 0.12) → pick volume vs precision.

---

## 5. Results log (append as we iterate)

> Backtest split fixed 2026-08-09: the temporal key was first-seen time
> (`listed_at`), but the local/prod DB is bulk-loaded, so that collapsed to a
> single day (1,807/1,833 listings share one timestamp) and the split was
> effectively random. `_split` now keys on `date_posted` (tie-safe) — the
> numbers below are on the honest temporal split. Cutoff: 2026-06-26.

| Date | LLM model | Feature set | MAPE | MdAPE | R² | Notes |
|---|---|---|---|---|---|---|
| 2026-08-09 | deepseek-v4-flash | +8 LLM (test window) | 27.4% | 18.9% | 0.511 | A/B on 367-row window; 80/365 identical to llama; **0/365** `condition=unknown` |
| 2026-08-09 | llama3.1:8b (local) | baseline | 40.2% | 27.9% | 0.374 | `--no-llm-features`, date_posted split |
| 2026-08-09 | llama3.1:8b (local) | +8 LLM | 27.4% | 18.9% | 0.511 | JSON mode on; 1,849/1,849 enriched |
| 2026-08-09 | llama3.1:8b (local) | +8 LLM, re-extracted | 28.8% | 19.9% | 0.488 | same provider, 148-row window; 33/148 identical → **run-to-run noise floor ≈ ±1.4pp** |
| 2026-08-09 | llama3.1:8b (local) | baseline (old split) | 31.9% | 23.8% | 0.417 | pre-fix, retained for reference |
| 2026-08-09 | llama3.1:8b (local) | +8 LLM (old split) | 26.2% | 19.1% | 0.569 | pre-fix, retained for reference |

> Interpreting provider A/Bs: the same provider re-extracted twice differs by
> ~1.4pp MAPE (extraction is sampled). Only trust a provider delta beyond that
> noise floor — ideally average 2–3 runs.
>
> **Provider verdict (2026-08-09):** deepseek-v4-flash matches llama3.1:8b on
> backtest MAPE (27.44% vs 27.4%, Δ≈0.04pp ≪ noise floor) while being cheaper
> and more disciplined (`condition=unknown` 0/365 vs 15/148 on llama's own
> re-run). Either is fine to switch to; there is no free win from a provider
> swap, so optimization effort is better spent on the §4 knobs.
