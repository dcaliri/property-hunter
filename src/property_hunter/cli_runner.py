"""Per-command runners wired to the CLI (cli.py dispatches here)."""

from __future__ import annotations

import argparse
import time

from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.logging_conf import elapsed_ms
from property_hunter.pipeline import run_collect


def _repo(settings: Settings) -> Repository:
    return Repository(connect(settings.db_path))


def _print_summary(stage: str, counts: dict, start: float) -> None:
    ms = elapsed_ms(start)
    parts = ", ".join(f"{k}={v}" for k, v in counts.items() if k != "run_id")
    print(f"[{stage}] completed in {ms}ms — {parts}")


def run_command(args: argparse.Namespace, settings: Settings) -> int:
    from property_hunter.db import init_db

    init_db(settings.db_path)  # idempotent schema bootstrap for all commands
    start = time.monotonic()
    command = args.command

    if command == "collect":
        scope = getattr(args, "scope", None)
        if scope:
            parts = scope.split("-en-")
            if len(parts) != 3:
                raise SystemExit(f"invalid --scope {scope!r}; expected <type>-en-<operation>-en-<region>")
            settings.scope.type, settings.scope.operation, settings.scope.region = parts
        summary = run_collect(settings, _repo(settings), offline=args.offline_fixtures,
                              max_pages=args.max_pages, delay=args.delay)
        _print_summary("collect", summary, start)
        return 0

    if command == "analyze":
        from property_hunter.analyze import run_analyze

        counts = run_analyze(settings, _repo(settings))
        _print_summary("analyze", counts, start)
        return 0

    if command == "train":
        from property_hunter.ml.train import run_train

        if getattr(args, "min_train_samples", None) is not None:
            settings.ml.min_train_samples = args.min_train_samples
        counts = run_train(settings, _repo(settings))
        _print_summary("train", counts, start)
        return 0

    if command == "predict":
        from property_hunter.ml.predict import run_predict

        counts = run_predict(settings, _repo(settings))
        _print_summary("predict", counts, start)
        return 0

    if command == "backtest":
        from property_hunter.ml.backtest import format_report, run_backtest

        if getattr(args, "min_train_samples", None) is not None:
            settings.ml.min_train_samples = args.min_train_samples
        result = run_backtest(settings, _repo(settings), cutoff=args.cutoff,
                              use_llm_features=not args.no_llm_features)
        print(format_report(result))
        return 0

    if command == "enrich-features":
        from property_hunter.llm.features_extract import extract_listing_features

        counts = extract_listing_features(settings, _repo(settings), limit=args.limit)
        _print_summary("enrich-features", counts, start)
        return 0

    if command == "detect":
        from property_hunter.detect import run_detect

        counts = run_detect(settings, _repo(settings))
        _print_summary("detect", counts, start)
        return 0

    if command == "notify":
        from property_hunter.notify.email import run_notify

        counts = run_notify(settings, _repo(settings))
        _print_summary("notify", counts, start)
        return 0

    if command == "run-all":
        from property_hunter.pipeline import run_all

        counts = run_all(settings, _repo(settings), offline=args.offline_fixtures)
        _print_summary("run-all", counts, start)
        return 0

    if command == "scheduler":
        from property_hunter.scheduler import run_scheduler

        run_scheduler(settings)
        return 0

    if command == "ui":
        from property_hunter.ui import serve

        serve(settings, host=args.host, port=args.port)
        return 0

    raise SystemExit(f"unknown command: {command}")
