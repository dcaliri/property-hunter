#!/usr/bin/env python3
"""A/B the LLM provider for structured feature extraction.

Re-extracts the backtest test-window listings with a different provider
(Groq, DeepSeek, OpenAI, Ollama, ...), then reports:

  * extraction agreement vs the currently stored features (per-field)
  * the temporal-backtest MAPE with the new provider's features on the
    test window, versus the current provider and the no-LLM baseline

The script snapshots the affected llm_features rows and restores them on
exit, so the production feature set is never mutated permanently. The
backtest model is trained on the current provider's features (80% train
window) and evaluated on the new provider's features (20% test window);
read the agreement numbers next to the MAPE to interpret the result.

Usage:
  GROQ_API_KEY=gsk_... scripts/compare_llm_providers.py --provider groq --max 50
  DEEPSEEK_API_KEY=sk-... scripts/compare_llm_providers.py --provider deepseek
  scripts/compare_llm_providers.py --base-url http://localhost:11434/v1 \
      --model llama3.1:8b --api-key ollama --label ollama
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from property_hunter.config import LLMConfig, Settings
from property_hunter.db import Repository, connect
from property_hunter.llm.client import complete
from property_hunter.llm.features_extract import _SYSTEM, _user_message, parse_features
from property_hunter.ml.backtest import _split, _usable, run_backtest
from property_hunter.util import utcnow

FIELDS = ("condition", "floor", "expensas", "orientation",
          "has_parking", "has_pool", "has_gym", "has_terrace",
          "has_balcony", "has_security")

PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-v4-flash", "DEEPSEEK_API_KEY"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", choices=sorted(PRESETS), help="Preset (key read from env)")
    p.add_argument("--base-url", help="Chat-completions base URL")
    p.add_argument("--model", help="Model name")
    p.add_argument("--api-key", help="API key")
    p.add_argument("--label", default=None, help="Report label (default: provider:model)")
    p.add_argument("--cutoff", default=None, help="ISO-8601 cutoff; default = backtest split")
    p.add_argument("--max", type=int, default=None, help="Only test the first N test-window rows")
    p.add_argument("--delay", type=float, default=1.0, help="Seconds between calls (rate limits)")
    p.add_argument("--no-json-mode", action="store_true",
                   help="Do not send response_format (some free models reject it)")
    p.add_argument("--report", help="Write a JSON report to this path")
    return p.parse_args()


def _provider_llm(args: argparse.Namespace, settings: Settings) -> tuple[LLMConfig, str]:
    env_key = ""
    if args.provider:
        base_url, model, env_key = PRESETS[args.provider]
        api_key = args.api_key or os.environ.get(env_key, "")
        model = args.model or model
    else:
        base_url = args.base_url
        model = args.model
        api_key = args.api_key
    if not (base_url and model and api_key):
        if env_key:
            sys.exit(f"missing {env_key} environment variable (or pass --api-key)")
        sys.exit("missing config: use --provider, or --base-url/--model/--api-key")
    label = args.label or f"{args.provider or base_url}:{model}"
    llm = LLMConfig(base_url=base_url, model=model, api_key=api_key,
                    timeout_seconds=settings.llm.timeout_seconds)
    return llm, label


def _metrics(result: dict) -> str:
    m = result.get("metrics") or {}
    return (f"MAPE={m.get('mape', float('nan')):.1%} "
            f"MdAPE={m.get('mdape', float('nan')):.1%} "
            f"R2={m.get('r2') if m.get('r2') is not None else float('nan'):.3f} "
            f"(train={result.get('train')} test={result.get('test')})")


def _load_old(row) -> dict:
    try:
        parsed = json.loads(row["llm_features"])
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _agreement(test, snapshot, new_features: dict[int, dict]) -> dict:
    field_stats = {f: {"agree": 0, "rows": 0} for f in FIELDS}
    full_agree = 0
    rows = 0
    for row in test:
        lid = row["id"]
        if lid not in new_features:
            continue
        old = _load_old(row)
        new = new_features[lid]
        rows += 1
        if all(old.get(f) == new.get(f) for f in FIELDS):
            full_agree += 1
        for f in FIELDS:
            field_stats[f]["rows"] += 1
            if old.get(f) == new.get(f):
                field_stats[f]["agree"] += 1
    unknown_new = sum(1 for f in new_features.values() if f.get("condition") == "unknown")
    return {
        "rows": rows,
        "full_agreement": full_agree,
        "fields": {f: (field_stats[f]["agree"] / field_stats[f]["rows"] if field_stats[f]["rows"] else None)
                   for f in FIELDS},
        "new_condition_unknown": unknown_new,
    }


def _restore(repo: Repository, snapshot: dict[int, tuple]) -> None:
    for lid, (features, updated_at) in snapshot.items():
        repo.conn.execute(
            "UPDATE listings SET llm_features=?, llm_features_updated_at=? WHERE id=?",
            (features, updated_at, lid))


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    args = parse_args()
    settings = Settings.from_env()
    llm, label = _provider_llm(args, settings)
    repo = Repository(connect(settings.db_path))

    rows = _usable(repo.sale_listings_history("sale"))
    train, test, split_at = _split(rows, args.cutoff, settings.ml.test_split)
    if args.max:
        test = test[: args.max]
    if not test:
        sys.exit("no test-window listings to re-extract")
    print(f"provider     : {label}")
    print(f"test window  : {len(test)} listings (cutoff={split_at})")

    baseline_llm = run_backtest(settings, repo, use_llm_features=True)
    baseline_nollm = run_backtest(settings, repo, use_llm_features=False)
    print(f"  current provider : {_metrics(baseline_llm)}")
    print(f"  no LLM features  : {_metrics(baseline_nollm)}")

    snapshot = {r["id"]: (r["llm_features"], r["llm_features_updated_at"]) for r in test}
    new_features: dict[int, dict] = {}
    failed = 0
    try:
        for i, row in enumerate(test):
            description = row["description"] or ""
            if not description.strip():
                continue
            try:
                raw = complete(
                    llm,
                    [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": _user_message(description)}],
                    json_mode=not args.no_json_mode,
                )
                new_features[row["id"]] = parse_features(raw)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  error listing {row['id']}: {exc}")
            if args.delay:
                time.sleep(args.delay)
            if (i + 1) % 25 == 0:
                print(f"  re-extracted {i + 1}/{len(test)}")
        if not new_features:
            sys.exit("no listings re-extracted — DB untouched")

        for lid, features in new_features.items():
            repo.conn.execute(
                "UPDATE listings SET llm_features=?, llm_features_updated_at=? WHERE id=?",
                (json.dumps(features), utcnow(), lid))
        repo.conn.commit()
        print(f"re-extracted : {len(new_features)} ok, {failed} failed")

        complete_extraction = len(new_features) == len(test)
        if complete_extraction:
            provider_run = run_backtest(settings, repo, use_llm_features=True)
            print(f"  provider ({label}): {_metrics(provider_run)}")
            delta = (provider_run["metrics"]["mape"] - baseline_llm["metrics"]["mape"])
            print(f"  MAPE delta vs current provider: {delta:+.1%}")
        else:
            provider_run = None
            print("  provider MAPE skipped: partial re-extraction (--max) would mix "
                  "providers in the test set; run without --max for a number")

        agreement = _agreement(test, snapshot, new_features)
        print(f"  agreement  : {agreement['full_agreement']}/{agreement['rows']} "
              f"listings fully identical")
        for f in FIELDS:
            rate = agreement["fields"][f]
            if rate is not None:
                print(f"    {f:<12} {rate:.1%}")
        print(f"  condition 'unknown' (new): "
              f"{agreement['new_condition_unknown']}/{agreement['rows']}")

        if args.report:
            payload = {
                "provider": label,
                "cutoff": split_at,
                "test_window": len(test),
                "re_extracted": len(new_features),
                "failed": failed,
                "agreement": agreement,
                "metrics": {
                    "baseline_no_llm": baseline_nollm["metrics"],
                    "baseline_current": baseline_llm["metrics"],
                    "provider": provider_run["metrics"] if provider_run else None,
                },
                "mape_delta_vs_current": (delta if provider_run else None),
            }
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            print(f"report       : {args.report}")
    finally:
        _restore(repo, snapshot)
        repo.conn.commit()
        print("restored DB features for the test window")


if __name__ == "__main__":
    main()
