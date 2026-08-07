# `.env` ML & LLM setup guide (beginner)

This explains the two optional-but-useful parts of Property Hunter's `.env`:

1. **ML** — a small local model that estimates what each apartment is worth.
2. **LLM** — an optional AI chat endpoint that writes nice digest summaries and
   tags listing descriptions.

Neither needs any external account to run the pipeline. ML is fully local
(sklearn, no API). LLM only does something if you give it a URL, a model name,
**and** an API key — otherwise it is skipped silently.

---

## 1. The ML valuation model

### What it does, in plain words

Every active sale listing gets a **predicted value** (in pesos). The model
looks at things like bedrooms, bathrooms, covered area, total area, how long
the ad has been up, and which barrio it's in — then compares the listing price
against what the model thinks it's worth. That comparison powers the
"undervalued" opportunity signal.

### The two knobs

| `.env` key | Default | What it controls |
|---|---|---|
| `ML_MIN_TRAIN_SAMPLES` | `200` | Minimum number of usable sale listings before the model will train at all. |
| `ML_TEST_SPLIT` | `0.2` | Fraction of data held back to *measure* how good the model is (see below). |

### How training works (`run_train` in `src/property_hunter/ml/train.py`)

1. Pull all active sale listings that have a positive price **and** at least one
   area value. These are the "usable" samples.
2. If the count is below `ML_MIN_TRAIN_SAMPLES`, training is **skipped** and
   every prediction falls back to the simple rule: *zone median price-per-m²
   × area*. (That fallback also applies per-listing whenever the model path
   fails.)
3. Builds the feature table: `beds`, `baths`, `covered_area_m2`,
   `total_area_m2`, `age_days` (days since the ad was posted), plus one-hot
   columns for barrio and property type. Training examples whose barrio/type
   weren't seen get all-zero flags.
4. Uses `ML_TEST_SPLIT` of the data purely for **evaluation** (a "test set"
   the model never trains on). The rest trains a
   `HistGradientBoostingRegressor` (a standard scikit-learn gradient-boosting
   model; `max_iter=150`, `learning_rate=0.08`, `min_samples_leaf=4`,
   `random_state=42` — these are fixed in code, not configurable via `.env`).
5. Because house prices span many magnitudes, the model learns on
   `log1p(price)`, and predictions are converted back with `expm1`.
6. Reports two quality numbers:
   - **R²** — how much of the price variation the model explains. `1.0` is
     perfect, `0.0` means it's no better than just predicting the average.
   - **MAE** (mean absolute error) — the average miss **in pesos** (cents / 100).
7. Saves a versioned "bundle" (model + feature vocabulary, pickled) into the
   `model_versions` table as the new *current* model.

### How predictions use it (`run_predict` in `src/property_hunter/ml/predict.py`)

For every active sale listing:

- If a current model exists → model estimate (recorded with
  `model_version_id` set, `is_fallback=0`).
- If the model path fails or the estimate isn't positive → fall back to
  *zone median price-per-m² × area* (`is_fallback=1`, no model reference).
- If even that isn't available (no zone baseline) → the listing is skipped.

### How to read the current model's health

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('file:data/property_hunter.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
print(dict(c.execute('SELECT id, training_count, r2_score, mae_cents, is_current FROM model_versions ORDER BY id DESC LIMIT 1').fetchone()))
"
```

Today's model (trained during run 7): **1,833 samples, R² ≈ 0.53, MAE ≈
$38.8k**. R² of 0.53 means the model captures a little over half the price
variation — good enough to flag rough undervaluation, not a precise appraisal.

### What happens if you change the knobs

- **Lower `ML_MIN_TRAIN_SAMPLES`** (e.g. `50`) → the model may train on small
  or one-barrio datasets. It stops skipping on thin data but R²/MAE can get
  noisy. Good for experiments.
- **Raise it** (e.g. `500`) → training is skipped more often on small scopes;
  you get more reliable models only once enough listings accumulate.
- **`ML_TEST_SPLIT`** affects only the *measured* R²/MAE, not the final model
  much. `0.2` is a reasonable default; `0.3` makes the quality estimate more
  conservative.

---

## 2. The optional LLM features

### What they do, in plain words

Two small AI features, both **fail-open** (if the AI is unreachable or
misconfigured, the pipeline continues and just skips these):

1. **Amenity tags** (`llm/enrich.py`): for each newly added listing description,
   one request asks the model to pick tags from a **fixed 18-word vocabulary**
   (e.g. `balcón`, `cochera`, `piscina`, `a estrenar`). Tags are stored on the
   listing and shown in the digest email.
2. **Digest narrative** (`llm/narrative.py`): one request per notify pass for a
   short Spanish summary that opens the digest email.

### The four knobs

| `.env` key | What it controls |
|---|---|
| `LLM_BASE_URL` | Base URL of any **OpenAI-compatible** chat endpoint, e.g. `https://api.openai.com/v1` or a local/self-hosted one. The code calls `{LLM_BASE_URL}/chat/completions`. |
| `LLM_MODEL` | Model name string, e.g. `gpt-4o-mini`, `llama-3.1-8b`. |
| `LLM_API_KEY` | Bearer token. **Never commit this to git.** |
| `LLM_TIMEOUT_SECONDS` | Per-request timeout (default `30`). |

### The catch to know

LLM is enabled **only if all three** of `LLM_BASE_URL`, `LLM_MODEL`, and
`LLM_API_KEY` are set (`LLMConfig.enabled`). In your current `.env` you have
`LLM_API_KEY` filled but `LLM_BASE_URL`/`LLM_MODEL` empty — so the LLM features
are effectively **off** right now (the key alone does nothing).

### Example: turn it on with OpenAI

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
LLM_TIMEOUT_SECONDS=30
```

Or with a local server (e.g. Ollama + a proxy, LM Studio, vLLM):

```bash
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1
LLM_API_KEY=any-non-empty-value
```

### Cost & behavior notes

- Each enriched description = 1 request; the narrative = 1 request per digest.
  On ~1,800 listings the first run with LLM on is ~1,800 requests, then only
  **new** listings (tracked via `llm_tags_updated_at`).
- Tags are validated against the fixed vocabulary — anything the model invents
  is dropped.
- Any timeout/error is logged (`llm.enrich failed (fail-open)`) and skipped;
  no local embedding model is used.

---

## Quick reference: other knobs that touch this area

| Key | Default | Meaning |
|---|---|---|
| `MIN_OBSERVATIONS_PER_ZONE` | `5` | Listings per barrio before a zone baseline is considered reliable. |
| `BASELINE_WINDOW_DAYS` | `90` | How far back observations are used for baselines/training window. |
| `UNDERVALUATION_THRESHOLD` | `0.10` | Flag when model value ≥ 10% above asking price. |
| `SMTP_HOST` / `ALERT_EMAIL` | empty | Set to actually receive the digest email. |

The `.env` only sets a minimum training size and the evaluation split; the
model algorithm, its hyperparameters, and the random seed are fixed in
`src/property_hunter/ml/train.py` (`MLConfig.random_state = 42`).
