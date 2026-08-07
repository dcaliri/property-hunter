"""CLI entrypoint for property-hunter.

Subcommands: init-db, collect, analyze, train, predict, detect, notify,
run-all, scheduler.
"""

from __future__ import annotations

import argparse
import sys

from property_hunter import __version__
from property_hunter.config import Settings
from property_hunter.logging_conf import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="property_hunter", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create/upgrade the SQLite schema")
    p_run_all = sub.add_parser("run-all", help="Run collect → analyze → train/predict → detect → notify")
    p_run_all.add_argument("--offline-fixtures", action="store_true",
                           help="Use saved fixture pages instead of the network")

    p_collect = sub.add_parser("collect", help="Collect listings from inmoup.com.ar")
    p_collect.add_argument("--offline-fixtures", action="store_true", help="Read saved fixture pages instead of the network")
    p_collect.add_argument("--max-pages", type=int, default=None)
    p_collect.add_argument("--delay", type=float, default=None)
    p_collect.add_argument("--scope", default=None,
                           help="Override scope slug, e.g. departamentos-en-venta-en-capital-federal")

    sub.add_parser("analyze", help="Compute per-zone baselines")
    p_train = sub.add_parser("train", help="Train the ML valuation model")
    p_train.add_argument("--min-train-samples", type=int, default=None, help="Override ML_MIN_TRAIN_SAMPLES")
    sub.add_parser("predict", help="Recompute value estimates for active listings")
    sub.add_parser("detect", help="Run opportunity detection rules")
    sub.add_parser("notify", help="Email digest of new opportunities")
    sub.add_parser("scheduler", help="Run the daily scheduler")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    return dispatch(args, settings)


def dispatch(args: argparse.Namespace, settings: Settings) -> int:
    from property_hunter.db import init_db

    command = args.command
    if command == "init-db":
        init_db(settings.db_path)
        print(f"Database initialized: {settings.db_path}")
        return 0

    # The remaining commands require a database; delegate to per-stage runners.
    from property_hunter.cli_runner import run_command

    return run_command(args, settings)


if __name__ == "__main__":
    sys.exit(main())
