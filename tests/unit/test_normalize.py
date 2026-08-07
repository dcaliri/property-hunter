"""User Story 1 tests: normalization and identity (tests-first, constitution IV)."""

from __future__ import annotations

import pytest

from property_hunter.ingest.extract import parse_detail_page, parse_list_page
from property_hunter.normalize import (
    normalize_listing,
    parse_source_listing_id,
    property_type_from_slug,
)

OBSERVED_AT = "2026-08-06T12:00:00+00:00"


@pytest.fixture(scope="module")
def list_item():
    items = parse_list_page(open("tests/fixtures/list_caba_deptos_venta_p1.html", encoding="utf-8").read())
    return items[0]


def test_source_listing_id_from_url():
    url = "https://inmoup.com.ar/167215-magma-emprendimientos/inmuebles/326/ficha/departamentos-en-venta-en-av-cordoba-5100"
    assert parse_source_listing_id(url) == 326


def test_normalize_identity_stable(list_item):
    rec1 = normalize_listing(list_item, operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    rec2 = normalize_listing(list_item, operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec1.source_listing_id == rec2.source_listing_id == 326
    assert rec1.source == "inmoup"
    assert rec1.operation == "sale"


def test_normalize_barrio_parsed_from_name(list_item):
    rec = normalize_listing(list_item, operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.barrio == "Palermo"
    assert rec.region == "Capital Federal"


def test_normalize_price_to_cents(list_item):
    rec = normalize_listing(list_item, operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.price_cents == list_item["offers"]["price"] * 100
    assert rec.currency == "USD"


def test_normalize_detail_page():
    item = parse_detail_page(open("tests/fixtures/detail_lezica_4100.html", encoding="utf-8").read())
    rec = normalize_listing(item, operation="sale", property_type="ph", observed_at=OBSERVED_AT)
    assert rec.source_listing_id == 49
    assert rec.barrio == "Almagro"
    assert rec.beds == 3
    assert rec.baths == 2
    assert rec.covered_area_m2 == 74
    assert rec.total_area_m2 == 164
    assert rec.street_address == "Lezica al 4100"


def test_normalize_unknown_barrio_bucket():
    """Listing with no parseable barrio is bucketed under 'unknown' in its region (SC-002)."""
    item = {
        "url": "https://inmoup.com.ar/agency/inmuebles/999/ficha/some-listing",
        "name": "Departamentos en Venta, 2 dormitorios, 1 baños",
        "offers": {"price": 100000, "priceCurrency": "USD"},
        "provider": {"name": "AGENCY", "address": {"addressLocality": None, "addressRegion": "Capital Federal"}},
        "additionalProperty": [],
    }
    rec = normalize_listing(item, operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.barrio == "unknown"
    assert rec.region == "Capital Federal"


def test_property_type_from_slug():
    assert property_type_from_slug("departamentos") == "departamento"
    assert property_type_from_slug("casas") == "casa"
    assert property_type_from_slug("lotes") == "lote"
    assert property_type_from_slug("whatever") == "otro"
