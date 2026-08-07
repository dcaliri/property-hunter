"""User Story 1 tests: JSON-LD extraction (tests-first, constitution IV)."""

from __future__ import annotations

from tests.conftest import load_fixture
from property_hunter.ingest.extract import parse_detail_page, parse_list_page


def test_extract_list_page_items():
    html = load_fixture("list_caba_deptos_venta_p1.html")
    items = parse_list_page(html)
    assert len(items) == 24
    first = items[0]
    assert first["url"].startswith("https://inmoup.com.ar/")
    assert "/inmuebles/" in first["url"]
    assert first["offers"]["price"] > 0
    assert first["offers"]["priceCurrency"] == "USD"
    props = {p["name"]: p["value"] for p in first.get("additionalProperty", [])}
    assert props.get("Dormitorios") == "1"
    assert props.get("Baños") == "1"
    assert float(props.get("Superficie")) > 0
    assert first["provider"]["address"]["addressRegion"] == "Capital Federal"


def test_extract_list_page_barrio_in_name():
    html = load_fixture("list_caba_deptos_venta_p1.html")
    items = parse_list_page(html)
    names = [it["name"] for it in items]
    assert any("en Palermo" in n for n in names)


def test_extract_detail_page():
    html = load_fixture("detail_lezica_4100.html")
    item = parse_detail_page(html)
    assert item is not None
    assert item["address"]["streetAddress"] == "Lezica al 4100"
    assert item["address"]["addressLocality"] == "Almagro"
    assert item["address"]["addressRegion"] == "Capital Federal"
    assert item["offers"]["price"] == 180000
    props = {p["name"]: p["value"] for p in item.get("additionalProperty", [])}
    assert props.get("Superficie Total m2") == "164"
    assert item.get("datePosted")


def test_pagination_completion():
    """Page 2 yields no new listing items: the collector must stop.

    inmoup's SEO JSON-LD embeds the first page of items on every page URL, so
    the completion signal is 'a page yields no new listing urls' (research §2).
    """
    p1 = parse_list_page(load_fixture("list_caba_deptos_venta_p1.html"))
    p2 = parse_list_page(load_fixture("list_caba_deptos_venta_p2.html"))
    p1_ids = {u.split("/inmuebles/")[1].split("/")[0] for u in (it["url"] for it in p1)}
    p2_ids = {u.split("/inmuebles/")[1].split("/")[0] for u in (it["url"] for it in p2)}
    assert p1_ids == p2_ids
    new_ids = p2_ids - p1_ids
    assert len(new_ids) == 0
