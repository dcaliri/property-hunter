"""Zone baseline computation (US2).

Computes per-zone (barrio+region) x operation x property_type medians over a
trailing window and stores immutable baseline rows with a sufficiency flag
(SC-002 zone-assignment / data quality).
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Iterable

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.models import BaselineRecord
from property_hunter.util import utcnow, window_bounds

logger = logging.getLogger("property_hunter.analyze")


def _median(values: Iterable[int]) -> int | None:
    values = [int(v) for v in values]
    if not values:
        return None
    return int(statistics.median(values))


def run_analyze(settings: Settings, repo: Repository, run_id: int | None = None) -> dict:
    """Recompute zone baselines for all active observations in the window."""
    created = run_id is None
    run_id = run_id or repo.create_run(trigger="analyze")
    window_start, window_end = window_bounds(settings.baselines.window_days)
    computed_at = utcnow()

    counts = {"zones": 0, "sufficient_zones": 0, "baselines": 0, "observations": 0}

    for operation in ("sale", "rent"):
        rows = repo.observations_in_window(operation, window_start, window_end)
        counts["observations"] += len(rows)
        groups: dict[tuple[str, str, str | None], list] = defaultdict(list)
        for row in rows:
            groups[(row["region"], row["barrio"], row["property_type"])].append(row)

        for (region, barrio, property_type), obs in groups.items():
            zone_id = repo.upsert_zone(region, barrio)
            prices = [o["price_cents"] for o in obs]
            per_m2 = []
            for o in obs:
                area = o["covered_area_m2"] or o["total_area_m2"]
                if area:
                    per_m2.append(int(round(o["price_cents"] / area)))
            baseline = BaselineRecord(
                zone_id=zone_id,
                operation=operation,  # type: ignore[arg-type]
                property_type=property_type,  # type: ignore[arg-type]
                window_start=window_start,
                window_end=window_end,
                observation_count=len(obs),
                is_sufficient=len(obs) >= settings.baselines.min_observations_per_zone,
                median_price_cents=_median(prices) if operation == "sale" else None,
                median_rent_cents=_median(prices) if operation == "rent" else None,
                median_price_per_m2_cents=_median(per_m2) if per_m2 else None,
                computed_at=computed_at,
            )
            repo.insert_baseline(baseline)
            counts["baselines"] += 1
            counts["zones"] += 1
            if baseline.is_sufficient:
                counts["sufficient_zones"] += 1

    if created:
        repo.finish_run(run_id, "ok", computed_at)
    repo.conn.commit()
    logger.info(
        "analyze complete",
        extra={"ctx_run_id": run_id, "ctx_baselines": counts["baselines"],
               "ctx_sufficient_zones": counts["sufficient_zones"]},
    )
    return counts
