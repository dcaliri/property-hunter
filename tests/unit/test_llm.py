"""Tests for the LLM enrichment + narrative stages (US4)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.llm.enrich import enrich_descriptions, parse_tags
from property_hunter.llm.narrative import build_narrative
from property_hunter.models import ListingRecord, PriceObservation


def _observed_at(days_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_settings(db_path: Path, *, enabled: bool = True) -> Settings:
    s = Settings.from_env()
    s.db_path = db_path
    if enabled:
        s.llm.base_url = "https://llm.example.local/v1"
        s.llm.model = "test-model"
        s.llm.api_key = "test-key"
        s.llm.timeout_seconds = 30.0
    else:
        s.llm.base_url = ""
        s.llm.model = ""
        s.llm.api_key = ""
    return s


def _repo(db_path: Path) -> Repository:
    return Repository(connect(db_path))


def _seed_descriptions(repo: Repository, descriptions: list[str]) -> list[int]:
    observed_at = _observed_at()
    run_id = repo.create_run()
    lids = []
    for i, desc in enumerate(descriptions):
        lid = repo.upsert_listing(ListingRecord(
            source="inmoup", source_listing_id=200_000 + i,
            source_url=f"https://inmoup.com.ar/inmuebles/{200_000 + i}",
            operation="sale", property_type="departamento", barrio="Palermo",
            region="Comuna 14", covered_area_m2=50.0, price_cents=5_000_000,
            description=desc, observed_at=observed_at,
        ))
        repo.insert_observation(PriceObservation(
            run_id=run_id, listing_id=lid, price_cents=5_000_000, observed_at=observed_at,
        ))
        lids.append(lid)
    repo.finish_run(run_id, "ok", observed_at)
    repo.conn.commit()
    return lids


class StubResponse:
    def __init__(self, content: str, status: int = 200):
        self._content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=self)

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class StubLLMTransport:
    def __init__(self, responses: list[str | BaseException]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None):
        self.calls.append(dict(url=url, json=json, headers=headers))
        if not self.responses:
            raise httpx.ReadTimeout("no responses")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return StubResponse(item)


def test_enrich_stores_validated_tags(db_path: Path):
    repo = _repo(db_path)
    lids = _seed_descriptions(repo, ["Balcón con parrilla y cochera. Renovado."])
    transport = StubLLMTransport(responses=['["balcón", "parrilla", "cochera", "helipuerto"]'])

    counts = enrich_descriptions(_make_settings(db_path), repo, transport=transport)

    assert counts["enriched"] == 1
    row = repo.get_listing(lids[0])
    tags = json.loads(row["llm_amenity_tags"])
    assert tags == ["balcón", "parrilla", "cochera"]
    assert row["llm_tags_updated_at"] is not None


def test_enrich_fails_open_on_malformed_response(db_path: Path):
    repo = _repo(db_path)
    lids = _seed_descriptions(repo, ["Descripción rara"])
    transport = StubLLMTransport(responses=["NO ES JSON NI NADA"])

    counts = enrich_descriptions(_make_settings(db_path), repo, transport=transport)

    assert counts["enriched"] == 1  # still processed, tags omitted
    row = repo.get_listing(lids[0])
    assert row["llm_amenity_tags"] is None
    assert row["llm_tags_updated_at"] is not None


def test_enrich_fails_open_on_error(db_path: Path):
    repo = _repo(db_path)
    lids = _seed_descriptions(repo, ["Otro aviso"])
    transport = StubLLMTransport(responses=[httpx.ReadTimeout("timeout")])

    counts = enrich_descriptions(_make_settings(db_path), repo, transport=transport)

    assert counts["failed"] == 1
    assert counts["enriched"] == 0
    assert repo.get_listing(lids[0])["llm_tags_updated_at"] is None


def test_narrative_renders_in_digest(db_path: Path):
    repo = _repo(db_path)
    _seed_descriptions(repo, ["x"])
    transport = StubLLMTransport(responses=["Hoy detectamos 3 oportunidades en Palermo."])

    narrative = build_narrative(_make_settings(db_path), repo, [{"id": 1, "listing_id": 2, "score": 0.33}],
                                transport=transport)

    assert "Palermo" in narrative
    assert len(transport.calls) == 1


def test_narrative_fallback_on_empty(db_path: Path):
    repo = _repo(db_path)
    _seed_descriptions(repo, ["x"])
    transport = StubLLMTransport(responses=[""])

    narrative = build_narrative(_make_settings(db_path), repo, [{"id": 1, "listing_id": 2, "score": 0.33}],
                                transport=transport)

    assert narrative == ""


def test_skipped_without_config(db_path: Path):
    repo = _repo(db_path)
    lids = _seed_descriptions(repo, ["desc 1", "desc 2"])

    counts = enrich_descriptions(_make_settings(db_path, enabled=False), repo)

    assert counts["skipped"] == 1
    assert counts["enriched"] == 0
    assert all(repo.get_listing(l)["llm_amenity_tags"] is None for l in lids)

    narrative = build_narrative(_make_settings(db_path, enabled=False), repo,
                                [{"id": 1, "listing_id": 2, "score": 0.33}])
    assert narrative == ""


def test_budget_one_request_per_description_and_timeout(db_path: Path):
    repo = _repo(db_path)
    _seed_descriptions(repo, ["desc uno", "desc dos"])
    transport = StubLLMTransport(responses=['["balcón"]', httpx.ReadTimeout("timeout")])

    counts = enrich_descriptions(_make_settings(db_path), repo, transport=transport)

    assert counts["requests"] == 2  # exactly one per new description
    assert counts["enriched"] == 1
    assert counts["failed"] == 1
    assert len(transport.calls) == 2


def test_parse_tags_validates_vocabulary():
    assert parse_tags('["balcón", "piscina", "NO_EXISTE"]') == ["balcón", "piscina"]
    assert parse_tags('balcón, parrilla') == ["balcón", "parrilla"]
    assert parse_tags('[]') == []
    assert parse_tags('') == []
