"""User Story 1 tests: dedupe, price history, delisting (tests-first, constitution IV)."""

from __future__ import annotations

from property_hunter.models import ListingRecord, PriceObservation
from property_hunter.util import utcnow


def _listing(source_listing_id: int, price_cents: int, operation: str = "sale") -> ListingRecord:
    return ListingRecord(
        source="inmoup",
        source_listing_id=source_listing_id,
        source_url=f"https://inmoup.com.ar/a/inmuebles/{source_listing_id}/ficha/x",
        operation=operation,
        property_type="departamento",
        barrio="Palermo",
        region="Capital Federal",
        price_cents=price_cents,
        currency="USD",
        observed_at=utcnow(),
    )


def test_reobserve_same_price_no_price_history(repo):
    rec = _listing(1, 100000)
    run = repo.create_run()
    lid = repo.upsert_listing(rec)
    repo.insert_observation(PriceObservation(run_id=run, listing_id=lid, price_cents=rec.price_cents,
                                             observed_at=rec.observed_at))
    assert len(repo.price_history_for(lid)) == 0
    assert len(repo.observations_in_window("sale", "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")) == 1


def test_price_change_appends_history(repo):
    rec1 = _listing(2, 100000)
    run1 = repo.create_run()
    lid = repo.upsert_listing(rec1)
    repo.insert_observation(PriceObservation(run_id=run1, listing_id=lid, price_cents=100000, observed_at=rec1.observed_at))

    rec2 = _listing(2, 95000)
    run2 = repo.create_run()
    repo.upsert_listing(rec2)
    repo.insert_observation(PriceObservation(run_id=run2, listing_id=lid, price_cents=95000, observed_at=rec2.observed_at))
    repo.insert_price_history(lid, 100000, 95000, "USD", rec2.observed_at, run2)

    history = repo.price_history_for(lid)
    assert len(history) == 1
    assert history[0]["old_price_cents"] == 100000
    assert history[0]["new_price_cents"] == 95000


def test_delisted_marked_inactive_history_preserved(repo):
    rec = _listing(3, 50000)
    run1 = repo.create_run()
    lid = repo.upsert_listing(rec)
    repo.insert_observation(PriceObservation(run_id=run1, listing_id=lid, price_cents=50000, observed_at=rec.observed_at))

    run2 = repo.create_run()
    repo.mark_inactive_unseen(run2)

    listing = repo.get_listing(lid)
    assert listing["is_active"] == 0
    assert len(repo.price_history_for(lid)) == 0
    assert repo.get_listing(lid) is not None


def test_upsert_persists_coordinates(repo):
    import pytest

    rec = _listing(5, 100000)
    rec.lat = -34.6118561
    rec.lng = -58.4245777
    lid = repo.upsert_listing(rec)

    row = repo.get_listing(lid)
    assert row["lat"] == pytest.approx(-34.6118561)
    assert row["lng"] == pytest.approx(-58.4245777)


def test_init_db_migrates_lat_lng(tmp_path):
    import sqlite3

    from property_hunter.db import init_db

    path = tmp_path / "pre_coords.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            source_listing_id INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            operation TEXT NOT NULL,
            property_type TEXT,
            street_address TEXT,
            barrio TEXT,
            region TEXT,
            beds INTEGER,
            baths INTEGER,
            covered_area_m2 REAL,
            total_area_m2 REAL,
            agency_name TEXT,
            description TEXT,
            date_posted TEXT,
            llm_amenity_tags TEXT,
            llm_tags_updated_at TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source, source_listing_id)
        )
    """)
    conn.commit()
    conn.close()

    init_db(path)

    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    conn.close()
    assert "lat" in cols
    assert "lng" in cols


def test_detection_supersede(repo):
    from property_hunter.models import DetectionRecord, Signal

    rec = _listing(4, 100000)
    run1 = repo.create_run()
    lid = repo.upsert_listing(rec)
    repo.insert_observation(PriceObservation(run_id=run1, listing_id=lid, price_cents=100000,
                                             observed_at=rec.observed_at))

    for run in (run1, repo.create_run()):
        repo.insert_detection(DetectionRecord(
            listing_id=lid, run_id=run, signals=[Signal(type="price_drop", threshold=0.05,
                                                        observed=0.06, expected=0.05, satisfied=True)],
            score=0.33, status="active", first_seen_at=utcnow(), last_seen_at=utcnow(), created_at=utcnow(),
        ))
        repo.supersede_detections(run)

    rows = list(repo.conn.execute("SELECT * FROM detections WHERE listing_id=? ORDER BY id", (lid,)))
    assert len(rows) == 2
    assert rows[0]["status"] == "superseded"
    assert rows[1]["status"] == "active"
    assert len(repo.active_detections()) == 1
