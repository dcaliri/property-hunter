"""Daily scheduler entrypoint (T046).

Blocks and runs ``run_all`` once per day at the configured hour/minute using
APScheduler's cron trigger. Used as the Docker default command.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from property_hunter.config import Settings
from property_hunter.db import Repository, connect, init_db
from property_hunter.pipeline import run_all

logger = logging.getLogger("property_hunter.scheduler")


def run_scheduler(settings: Settings) -> None:
    init_db(settings.db_path)
    scheduler = BlockingScheduler(timezone="UTC")
    trigger = CronTrigger(hour=settings.schedule.daily_hour,
                          minute=settings.schedule.daily_minute)

    def job() -> None:
        repo = Repository(connect(settings.db_path))
        try:
            counts = run_all(settings, repo)
            logger.info("scheduled run-all finished", extra={
                "ctx_status": counts.get("_status")})
        except Exception:
            logger.exception("scheduled run-all failed")
        finally:
            repo.conn.close()

    scheduler.add_job(job, trigger, id="daily-run-all", replace_existing=True)
    logger.info("scheduler started", extra={
        "ctx_hour": settings.schedule.daily_hour,
        "ctx_minute": settings.schedule.daily_minute})
    scheduler.start()
