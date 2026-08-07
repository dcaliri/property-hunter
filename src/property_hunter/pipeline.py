"""Pipeline orchestrator: collect → analyze → train/predict → detect → notify.

Each stage is an independently callable function over the repository
(constitution V). ``run_collect`` is the US1 stage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.ingest.extract import parse_list_page
from property_hunter.ingest.inmoup import build_search_url, make_fetch
from property_hunter.models import PriceObservation
from property_hunter.normalize import operation_from_slug, property_type_from_slug, normalize_listing
from property_hunter.util import utcnow

logger = logging.getLogger("property_hunter.pipeline")


def _zone_assignment_rate(repo: Repository) -> float:
    """Fraction of active listings assigned to a zone (region not empty). SC-002."""
    total = repo.conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0]
    if total == 0:
        return 0.0
    assigned = repo.conn.execute(
        "SELECT COUNT(*) FROM listings WHERE is_active=1 AND region <> ''"
    ).fetchone()[0]
    return assigned / total


def _fixture_bytes() -> list[bytes]:
    base = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"
    return [
        (base / "list_caba_deptos_venta_p1.html").read_bytes(),
        (base / "list_caba_deptos_venta_p2.html").read_bytes(),
    ]


def run_collect(settings: Settings, repo: Repository, offline: bool = False,
                max_pages: int | None = None, delay: float | None = None,
                run_id: int | None = None) -> dict:
    """Collect listings for the configured scope and store them with provenance.

    Completion detection (research §2): stop when a page yields no new listing
    items (inmoup embeds the first page of items in its SEO JSON-LD).
    """
    started = utcnow()
    created = run_id is None
    run_id = run_id or repo.create_run(trigger="manual", started_at=started)

    collect = settings.collect
    page_limit = max_pages or collect.max_pages_per_search
    throttle = delay if delay is not None else collect.delay_seconds

    fetch = make_fetch(collect, offline=offline, fixtures=_fixture_bytes() if offline else None)

    operation = operation_from_slug(settings.scope.operation)
    property_type = property_type_from_slug(settings.scope.type)

    counts = {"fetched_pages": 0, "new_listings": 0, "price_changes": 0, "delisted": 0, "total_active": 0}
    seen_in_run: set[int] = set()

    try:
        for page in range(1, page_limit + 1):
            url = build_search_url(settings.scope.operation, settings.scope.type, settings.scope.region, page)
            status_code, body = fetch(url)
            page_id = repo.insert_page(run_id, url, utcnow(), status_code, body)
            counts["fetched_pages"] += 1

            if status_code != 200 or not body:
                logger.warning("empty or non-200 page; stopping", extra={"ctx_url": url, "ctx_status": status_code})
                break

            html = body.decode("utf-8", errors="replace")
            items = parse_list_page(html)
            new_in_page = 0
            for item in items:
                rec = normalize_listing(item, operation, property_type, started, url)
                if rec.source_listing_id in seen_in_run:
                    continue
                seen_in_run.add(rec.source_listing_id)
                if rec.price_cents <= 0:
                    logger.warning("skipping listing without price", extra={"ctx_url": rec.source_url})
                    continue
                listing_id = repo.upsert_listing(rec)
                prev = repo.previous_observation(listing_id)
                if prev is None:
                    counts["new_listings"] += 1
                    new_in_page += 1
                    repo.insert_observation(PriceObservation(
                        run_id=run_id, listing_id=listing_id, price_cents=rec.price_cents,
                        currency=rec.currency, observed_at=started, page_id=page_id))
                    repo.insert_price_history(listing_id, None, rec.price_cents, rec.currency, started, run_id)
                else:
                    if prev["price_cents"] != rec.price_cents:
                        counts["price_changes"] += 1
                        repo.insert_price_history(listing_id, prev["price_cents"], rec.price_cents,
                                                  rec.currency, started, run_id)
                    repo.insert_observation(PriceObservation(
                        run_id=run_id, listing_id=listing_id, price_cents=rec.price_cents,
                        currency=rec.currency, observed_at=started, page_id=page_id))

            if new_in_page == 0:
                logger.info("no new listings on page %d; completion", page, extra={"ctx_run_id": run_id})
                break

        counts["delisted"] = repo.mark_inactive_unseen(run_id)
        counts["total_active"] = len(repo.active_listings())
        counts["zone_assignment_rate"] = _zone_assignment_rate(repo)
        repo.conn.commit()
        if created:
            repo.finish_run(run_id, "ok", utcnow())
        repo.conn.commit()
        logger.info("collect complete", extra={"ctx_run_id": run_id, **{f"ctx_{k}": v for k, v in counts.items()}})
    except Exception:
        if created:
            repo.finish_run(run_id, "failed", utcnow())
            repo.conn.commit()
        logger.exception("collect failed", extra={"ctx_run_id": run_id})
        raise

    counts["run_id"] = run_id
    return counts


def run_all(settings: Settings, repo: Repository, offline: bool = False) -> dict:
    """Run the full pipeline in one pass sharing a single run_id (T045).

    Per-stage failures are captured and logged; later stages still run.
    Train/predict degrade to fallback-safe predictions on insufficient data.
    """
    run_id = repo.create_run(trigger="run-all", started_at=utcnow())
    from property_hunter.analyze import run_analyze
    from property_hunter.detect import run_detect
    from property_hunter.ml.predict import run_predict
    from property_hunter.ml.train import run_train
    from property_hunter.notify.email import run_notify

    stages = [
        ("collect", lambda: run_collect(settings, repo, run_id=run_id, offline=offline)),
        ("analyze", lambda: run_analyze(settings, repo, run_id=run_id)),
        ("train", lambda: run_train(settings, repo, run_id=run_id)),
        ("predict", lambda: run_predict(settings, repo, run_id=run_id)),
        ("detect", lambda: run_detect(settings, repo, run_id=run_id)),
        ("notify", lambda: run_notify(settings, repo, run_id=run_id)),
    ]
    statuses: dict[str, str] = {}
    counts: dict[str, dict] = {}
    for name, fn in stages:
        try:
            counts[name] = fn()
            statuses[name] = "ok"
        except Exception:
            statuses[name] = "failed"
            logger.exception("run-all stage failed", extra={"ctx_run_id": run_id, "ctx_stage": name})
            repo.finish_run(run_id, "partial", utcnow())
            repo.conn.commit()

    final_status = "ok" if all(s == "ok" for s in statuses.values()) else "partial"
    repo.finish_run(run_id, final_status, utcnow())
    repo.conn.commit()
    logger.info("run-all complete", extra={
        "ctx_run_id": run_id, "ctx_status": final_status,
        "ctx_stages": ",".join(f"{k}={v}" for k, v in statuses.items())})
    counts["run_id"] = run_id
    counts["_status"] = statuses
    return counts
