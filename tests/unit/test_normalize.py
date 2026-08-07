"""User Story 1 tests: normalization and identity (tests-first, constitution IV)."""

from __future__ import annotations

import pytest

from property_hunter.ingest.extract import parse_detail_page, parse_list_page
from property_hunter.normalize import (
    normalize_listing,
    parse_coordinates,
    parse_source_listing_id,
    property_type_from_slug,
)

OBSERVED_AT = "2026-08-06T12:00:00+00:00"


@pytest.fixture(scope="module")
def list_items():
    return parse_list_page(open("tests/fixtures/list_caba_deptos_venta_p1.html", encoding="utf-8").read())


def test_source_listing_id_from_url():
    url = "https://inmoup.com.ar/167215-magma-emprendimientos/inmuebles/326/ficha/departamentos-en-venta-en-av-cordoba-5100"
    assert parse_source_listing_id(url) == 326


def test_normalize_identity_stable(list_items):
    rec1 = normalize_listing(list_items[0], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    rec2 = normalize_listing(list_items[0], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec1.source_listing_id == rec2.source_listing_id == 326
    assert rec1.source == "inmoup"
    assert rec1.operation == "sale"


def test_normalize_barrio_from_localidad(list_items):
    rec = normalize_listing(list_items[0], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.barrio == "Palermo"
    assert rec.region == "Capital Federal"


def test_normalize_barrio_not_from_agency(list_items):
    """Barrio comes from the property's localidad, not the agency's address."""
    by_id = {it["id"]: it for it in list_items}
    rec = normalize_listing(by_id[8367], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.barrio == "Almagro"  # agency's office is Villa Urquiza


def test_normalize_stores_exact_coordinates(list_items):
    by_id = {it["id"]: it for it in list_items}
    rec = normalize_listing(by_id[49], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.lat == pytest.approx(-34.6118561)
    assert rec.lng == pytest.approx(-58.4245777)


def test_normalize_price_to_cents(list_items):
    rec = normalize_listing(list_items[0], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.price_cents == list_items[0]["precio"] * 100
    assert rec.currency == "USD"


def test_normalize_list_page_attributes(list_items):
    rec = normalize_listing(list_items[0], operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.beds == 1
    assert rec.baths == 1
    assert rec.covered_area_m2 == 50.0
    assert rec.total_area_m2 == 60.0
    assert rec.agency_name == "MAGMA EMPRENDIMIENTOS"
    assert rec.street_address == "av cordoba 5100"
    assert rec.description


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
    assert rec.lat == pytest.approx(-34.6118561)
    assert rec.lng == pytest.approx(-58.4245777)


def test_normalize_unknown_barrio_bucket():
    """Listing with no localidad is bucketed under 'unknown' in its region (SC-002)."""
    item = {
        "url": "https://inmoup.com.ar/agency/inmuebles/999/ficha/some-listing",
        "name": "Departamentos en Venta, 2 dormitorios, 1 baños",
        "offers": {"price": 100000, "priceCurrency": "USD"},
        "provider": {"name": "AGENCY", "address": {"addressLocality": "Palermo", "addressRegion": "Capital Federal"}},
        "additionalProperty": [],
    }
    rec = normalize_listing(item, operation="sale", property_type="departamento", observed_at=OBSERVED_AT)
    assert rec.barrio == "unknown"
    assert rec.region == "Capital Federal"


def test_parse_coordinates():
    assert parse_coordinates("-34.6118561, -58.4245777") == (-34.6118561, -58.4245777)
    assert parse_coordinates("not coords") == (None, None)
    assert parse_coordinates(None) == (None, None)
    assert parse_coordinates("999, -58") == (None, None)


def test_property_type_from_slug():
    assert property_type_from_slug("departamentos") == "departamento"
    assert property_type_from_slug("casas") == "casa"
    assert property_type_from_slug("lotes") == "lote"
    assert property_type_from_slug("whatever") == "otro"
