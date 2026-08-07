"""Unit tests for the local read-only web dashboard (ui.py)."""

from __future__ import annotations

import json
import threading
import urllib.request

from property_hunter.db import Repository
from property_hunter.models import ListingRecord, PriceObservation
from property_hunter.ui import UIHTTPServer, read_data


def test_read_data_shape(db_path):
    from property_hunter.db import connect

    conn = connect(db_path)
    repo = Repository(conn)
    run_id = repo.create_run()
    rec = ListingRecord(
        source_listing_id=1,
        source_url="https://inmoup.com.ar/agency/inmuebles/1/ficha/listing",
        operation="sale",
        barrio="Palermo",
        region="Capital Federal",
        price_cents=15000000,
        currency="ARS",
        observed_at="2026-08-07T12:00:00+00:00",
    )
    listing_id = repo.upsert_listing(rec)
    repo.insert_observation(PriceObservation(
        run_id=run_id, listing_id=listing_id, price_cents=rec.price_cents, observed_at=rec.observed_at))
    conn.commit()
    conn.close()

    data = read_data(db_path)
    assert "error" not in data
    assert data["db_path"].endswith("test.db")
    assert len(data["listings"]) == 1
    assert data["listings"][0]["barrio"] == "Palermo"
    assert data["listings"][0]["ask_cents"] == 15000000
    assert data["runs"][0]["id"] == run_id


def test_read_data_missing_db(tmp_path):
    data = read_data(tmp_path / "nope.db")
    assert "error" in data


def test_server_serves_dashboard_and_api(db_path):
    server = UIHTTPServer(db_path, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/") as resp:
            html = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "<title>Property Hunter</title>" in html
        assert 'id="help"' in html
        assert "How to read this dashboard" in html

        with urllib.request.urlopen(base + "/api/data") as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["db_path"].endswith("test.db")
        assert "listings" in payload and "runs" in payload and "detections" in payload
    finally:
        server.shutdown()
        server.server_close()
