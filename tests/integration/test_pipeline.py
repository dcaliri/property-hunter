"""User Story 1 integration: full collect stage over fixture pages (tests-first)."""

from __future__ import annotations

from pathlib import Path

from property_hunter.db import Repository, connect
from property_hunter.pipeline import run_collect
from tests.conftest import db_path  # noqa: F401


def _repo(db_path):
    return Repository(connect(db_path))


def test_collect_fixtures(db_path):
    settings = type("S", (), {})()  # placeholder replaced below
    settings = _make_settings(db_path)
    summary = run_collect(settings, _repo(db_path), offline=True)
    assert summary["new_listings"] == 24
    assert summary["fetched_pages"] >= 1

    repo = _repo(db_path)
    listings = repo.active_listings()
    assert len(listings) == 24
    assert all(l["source"] == "inmoup" for l in listings)

    pages = repo.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    assert pages >= 1


def test_collect_fixtures_no_duplicates_on_rerun(db_path):
    settings = _make_settings(db_path)
    repo = _repo(db_path)
    first = run_collect(settings, repo, offline=True)
    second = run_collect(settings, repo, offline=True)
    assert second["new_listings"] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == first["new_listings"]


def test_analyze_fixtures(db_path):
    from property_hunter.analyze import run_analyze

    settings = _make_settings(db_path, min_obs=2)
    repo = _repo(db_path)
    run_collect(settings, repo, offline=True)

    counts = run_analyze(settings, repo)

    assert counts["observations"] == 24
    assert counts["baselines"] >= 10
    assert counts["zones"] >= 10
    assert counts["sufficient_zones"] >= 1
    for bl in repo.latest_baselines():
        assert bl["median_price_per_m2_cents"] is not None
        assert bl["observation_count"] >= 1


def test_train_fixtures(db_path):
    from property_hunter.analyze import run_analyze
    from property_hunter.ml.predict import run_predict
    from property_hunter.ml.train import run_train

    settings = _make_settings(db_path, min_obs=1, min_train_samples=5)
    repo = _repo(db_path)
    run_collect(settings, repo, offline=True)
    run_analyze(settings, repo)

    train_counts = run_train(settings, repo)
    assert train_counts["samples"] == 24
    assert train_counts["skipped"] == 0
    mv = repo.current_model_version()
    assert mv is not None

    predict_counts = run_predict(settings, repo)
    assert predict_counts["predicted"] == 24
    assert predict_counts["fallback"] == 0
    assert predict_counts["model_id"] == mv["id"]


def test_detect_fixtures(db_path):
    import json
    import random

    import jsonschema
    from property_hunter.analyze import run_analyze
    from property_hunter.detect import run_detect
    from property_hunter.ml.predict import run_predict
    from property_hunter.ml.train import run_train
    from property_hunter.models import ListingRecord, PriceObservation
    from property_hunter.util import utcnow

    settings = _make_settings(db_path, min_obs=1, min_train_samples=5)
    repo = _repo(db_path)

    observed_at = utcnow()
    seed_run = repo.create_run()
    rng = random.Random(11)
    drop_lid = None
    for i in range(25):
        covered = rng.choice([40, 55, 70, 85])
        price = int(5_000_000 + covered * 60_000 + rng.uniform(-30_000, 30_000))
        lid = repo.upsert_listing(ListingRecord(
            source="inmoup", source_listing_id=90_000 + i,
            source_url=f"https://inmoup.com.ar/inmuebles/{90_000 + i}",
            operation="sale", property_type="departamento", barrio="Palermo",
            region="Comuna 14", beds=2, covered_area_m2=float(covered),
            price_cents=price, observed_at=observed_at,
        ))
        repo.insert_observation(PriceObservation(run_id=seed_run, listing_id=lid,
                                                 price_cents=price, observed_at=observed_at))
        if i == 24:
            drop_lid = lid
            repo.insert_price_history(lid, int(price * 1.10), price, "USD", observed_at, seed_run)
    # rent baseline so the yield rule can evaluate
    repo.upsert_listing(ListingRecord(
        source="inmoup", source_listing_id=99_999,
        source_url="https://inmoup.com.ar/inmuebles/99999",
        operation="rent", property_type="departamento", barrio="Palermo",
        region="Comuna 14", covered_area_m2=50.0,
        price_cents=500_000, observed_at=observed_at,
    ))
    repo.insert_observation(PriceObservation(run_id=seed_run, listing_id=repo.conn.execute(
        "SELECT id FROM listings WHERE source_listing_id=99999").fetchone()["id"],
        price_cents=500_000, observed_at=observed_at))
    repo.finish_run(seed_run, "ok", observed_at)
    repo.conn.commit()

    run_analyze(settings, repo)
    run_train(settings, repo)
    run_predict(settings, repo)
    counts = run_detect(settings, repo)

    assert counts["evaluated"] == 25
    assert counts["detections"] >= 1

    schema = json.loads(Path("specs/001-opportunity-hunter/contracts/detection-v1.schema.json").read_text())
    dets = repo.active_detections()
    assert dets, "expected at least one active detection"
    assert len(dets) == counts["detections"]
    for det in dets:
        payload = {k: v for k, v in dict(det).items() if k != "id"}
        payload["signals"] = json.loads(payload["signals"])
        jsonschema.validate(payload, schema)

    drop_det = next(
        d for d in dets
        if d["listing_id"] == drop_lid
        and any(s["type"] == "price_drop" and s["satisfied"] for s in json.loads(d["signals"]))
    )
    signals = json.loads(drop_det["signals"])
    pd = next(s for s in signals if s["type"] == "price_drop")
    hist = repo.price_history_for(drop_lid)[0]
    expected_drop = (hist["old_price_cents"] - hist["new_price_cents"]) / hist["old_price_cents"]
    assert abs(pd["observed"] - expected_drop) < 1e-9
    assert pd["expected"] == 0.05


def test_notify_smtp_null(db_path):
    import random

    from property_hunter.analyze import run_analyze
    from property_hunter.detect import run_detect
    from property_hunter.ml.predict import run_predict
    from property_hunter.ml.train import run_train
    from property_hunter.models import ListingRecord, PriceObservation
    from property_hunter.notify.email import run_notify
    from property_hunter.util import utcnow

    settings = _make_settings(db_path, min_obs=1, min_train_samples=5)
    settings.smtp.recipient = "me@example.com"
    settings.smtp.sender = "hunter@example.com"
    settings.notify.retry_backoff_base_seconds = 0.001
    repo = _repo(db_path)

    observed_at = utcnow()
    run = repo.create_run()
    rng = random.Random(3)
    for i in range(5):
        covered = rng.choice([40, 55, 70])
        price = int(4_000_000 + covered * 50_000)
        lid = repo.upsert_listing(ListingRecord(
            source="inmoup", source_listing_id=110_000 + i,
            source_url=f"https://inmoup.com.ar/inmuebles/{110_000 + i}",
            operation="sale", property_type="departamento", barrio="Caballito",
            region="Comuna 6", beds=2, covered_area_m2=float(covered),
            price_cents=price, observed_at=observed_at,
        ))
        repo.insert_observation(PriceObservation(run_id=run, listing_id=lid,
                                                 price_cents=price, observed_at=observed_at))
        if i == 0:
            repo.insert_price_history(lid, int(price * 1.10), price, "USD", observed_at, run)
    repo.finish_run(run, "ok", observed_at)
    repo.conn.commit()

    run_analyze(settings, repo)
    run_train(settings, repo)
    run_predict(settings, repo)
    run_detect(settings, repo)
    assert len(repo.unnotified_detections()) >= 1

    class StubTransport:
        def send(self, *, sender, recipient, subject, html, text):
            self.calls.append((sender, recipient, subject))
            self.calls_extra = None

    stub = StubTransport()
    stub.calls = []

    first = run_notify(settings, repo, transport=stub)
    second = run_notify(settings, repo, transport=stub)

    assert first["sent"] >= 1
    assert len(stub.calls) == 1
    assert second["sent"] == 0
    rows = list(repo.conn.execute("SELECT * FROM notifications"))
    assert len(rows) == len(repo.active_detections())


def test_notify_llm_narrative(db_path):
    import os

    from property_hunter.analyze import run_analyze
    from property_hunter.db import init_db
    from property_hunter.detect import run_detect
    from property_hunter.models import ListingRecord, PriceObservation
    from property_hunter.ml.predict import run_predict
    from property_hunter.ml.train import run_train
    from property_hunter.notify.email import run_notify
    from property_hunter.util import utcnow

    def seed_and_notify(use_llm: bool, path) -> list:
        init_db(path)
        settings = _make_settings(path, min_obs=1, min_train_samples=5)
        settings.smtp.recipient = "me@example.com"
        settings.smtp.sender = "hunter@example.com"
        settings.notify.retry_backoff_base_seconds = 0.001
        if use_llm:
            settings.llm.base_url = "https://llm.example.local/v1"
            settings.llm.model = "m"
            settings.llm.api_key = "k"
        repo = _repo(path)

        observed_at = utcnow()
        run = repo.create_run()
        lid = repo.upsert_listing(ListingRecord(
            source="inmoup", source_listing_id=120_001,
            source_url="https://inmoup.com.ar/inmuebles/120001",
            operation="sale", property_type="departamento", barrio="Belgrano",
            region="Comuna 13", beds=2, covered_area_m2=60.0,
            price_cents=8_500_000, observed_at=observed_at,
        ))
        repo.insert_observation(PriceObservation(run_id=run, listing_id=lid,
                                                 price_cents=8_500_000, observed_at=observed_at))
        repo.insert_price_history(lid, 9_500_000, 8_500_000, "USD", observed_at, run)
        repo.finish_run(run, "ok", observed_at)
        repo.conn.commit()

        run_analyze(settings, repo)
        run_train(settings, repo)
        run_predict(settings, repo)
        run_detect(settings, repo)

        class StubTransport:
            def send(self, **kwargs):
                self.calls.append(kwargs)

        stub = StubTransport()
        stub.calls = []

        class StubLLM:
            def post(self, url, json=None, headers=None):
                return type("R", (), {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"choices": [{"message": {"content": "Hoy detectamos oportunidades en Belgrano."}}]},
                })()

        run_notify(settings, repo, transport=stub, llm_transport=StubLLM())
        return list(repo.conn.execute("SELECT * FROM notifications"))

    llm_rows = seed_and_notify(use_llm=True, path=db_path.parent / "llm.db")
    assert len(llm_rows) == 1
    assert llm_rows[0]["llm_enriched"] == 1
    assert "Belgrano" in llm_rows[0]["digest_narrative"]

    plain_rows = seed_and_notify(use_llm=False, path=db_path.parent / "plain.db")
    assert len(plain_rows) == 1
    assert plain_rows[0]["llm_enriched"] == 0
    assert plain_rows[0]["digest_narrative"] is None


def _make_settings(db_path, *, min_obs=None, min_train_samples=None):
    from property_hunter.config import Settings

    s = Settings.from_env()
    if min_obs is not None:
        s.baselines.min_observations_per_zone = min_obs
    if min_train_samples is not None:
        s.ml.min_train_samples = min_train_samples
    return type("S", (), dict(db_path=db_path, scope=s.scope, collect=s.collect, baselines=s.baselines,
                             rules=s.rules, ml=s.ml, smtp=s.smtp, notify=s.notify, llm=s.llm,
                             schedule=s.schedule))()
