"""User Story 1 tests: extraction (tests-first, constitution IV).

List pages are parsed from the Next.js RSC ``initialItems`` payload (exact
coordinates, property ``localidad``, address, price) enriched with JSON-LD
description/datePosted. JSON-LD alone is exercised via ``parse_ld_list_page``.
"""

from __future__ import annotations

from tests.conftest import load_fixture
from property_hunter.ingest.extract import (
    api_items_to_cards,
    parse_detail_page,
    parse_ld_list_page,
    parse_list_page,
    parse_rsc_filters,
    parse_rsc_list_payload,
)


def test_extract_list_page_items():
    html = load_fixture("list_caba_deptos_venta_p1.html")
    items = parse_list_page(html)
    assert len(items) == 24
    first = items[0]
    assert first["url"].startswith("https://inmoup.com.ar/")
    assert "/inmuebles/" in first["url"]
    assert first["precio"] > 0
    assert first["localidad"]
    assert first["provincia"] == "Capital Federal"
    assert first["coordenadas"]
    assert first["direccion"]


def test_extract_rsc_payload_count():
    html = load_fixture("list_caba_deptos_venta_p1.html")
    items = parse_rsc_list_payload(html)
    assert len(items) == 24
    assert all(isinstance(it.get("id"), int) for it in items)
    assert all("coordenadas" in it for it in items)
    assert all(it.get("localidad") for it in items)


def test_extract_ld_list_page_items():
    html = load_fixture("list_caba_deptos_venta_p1.html")
    items = parse_ld_list_page(html)
    assert len(items) == 24
    first = items[0]
    assert first["url"].startswith("https://inmoup.com.ar/")
    assert first["offers"]["price"] > 0
    assert first["offers"]["priceCurrency"] == "USD"


def test_extract_rsc_coordinates_match_marker():
    """Coordinates extracted per listing are the exact map-marker coordinates."""
    items = parse_list_page(load_fixture("list_caba_deptos_venta_p1.html"))
    by_id = {it["id"]: it for it in items}
    assert by_id[49]["coordenadas"] == "-34.6118561, -58.4245777"
    assert by_id[8367]["coordenadas"] == "-34.6057807, -58.4258741"


def test_extract_barrio_is_property_localidad_not_agency():
    """The listing's barrio must be its own localidad, never the agency's."""
    items = parse_list_page(load_fixture("list_caba_deptos_venta_p1.html"))
    by_id = {it["id"]: it for it in items}
    item = by_id[8367]
    assert item["localidad"] == "Almagro"
    agency = item["inmobiliaria"]
    assert agency["nombre"] == "Coldwell Banker Lion Team"
    assert agency["localidad"] == "Villa Urquiza"  # the old (wrong) source
    assert agency["localidad"] != item["localidad"]


def test_extract_list_page_enriches_description():
    html = load_fixture("list_caba_deptos_venta_p1.html")
    items = parse_list_page(html)
    assert all(it.get("description") for it in items)


def test_extract_detail_page():
    html = load_fixture("detail_lezica_4100.html")
    item = parse_detail_page(html)
    assert item is not None
    assert item["address"]["streetAddress"] == "Lezica al 4100"
    assert item["address"]["addressLocality"] == "Almagro"
    assert item["address"]["addressRegion"] == "Capital Federal"
    assert item["geo"]["latitude"] == -34.6118561
    assert item["geo"]["longitude"] == -58.4245777
    assert item["offers"]["price"] == 180000
    props = {p["name"]: p["value"] for p in item.get("additionalProperty", [])}
    assert props.get("Superficie Total m2") == "164"
    assert item.get("datePosted")


def test_pagination_completion():
    """Page 2 yields no new listing items: the collector must stop.

    inmoup re-embeds the same first-page cards in its RSC payload on every page
    URL, so the SSR-only completion signal is 'a page yields no new listing ids'
    (research §2). The live collector now bypasses SSR pagination entirely via
    the site's JSON API (``parse_rsc_filters`` → ``/server/inmuebles/buscar``),
    which is why this offline fixture pair only ever yields one page of items.
    """
    p1 = parse_list_page(load_fixture("list_caba_deptos_venta_p1.html"))
    p2 = parse_list_page(load_fixture("list_caba_deptos_venta_p2.html"))
    p1_ids = {it["id"] for it in p1}
    p2_ids = {it["id"] for it in p2}
    assert p1_ids == p2_ids
    assert len(p2_ids - p1_ids) == 0


def _rsc_html(inner: str) -> str:
    """Wrap ``inner`` in a RSC push chunk (the JSON string is double-encoded)."""
    return f'<script>self.__next_f.push([1,{__import__("json").dumps(inner)}])</script>'


def test_parse_rsc_filters():
    html = _rsc_html('{"data":{},"filters":{"grupo":2,"condiciones":1,"provincias":[1]},"initialItems":[]}')
    assert parse_rsc_filters(html) == {"grupo": 2, "condiciones": 1, "provincias": [1]}


def test_parse_rsc_filters_absent_or_shaped_differently():
    assert parse_rsc_filters("<html><body>no rsc payload</body></html>") is None
    html = _rsc_html('{"filters":{"rango":{"min":0,"max":0}}}')  # nested object, no grupo/condiciones
    assert parse_rsc_filters(html) is None


def test_api_items_to_cards():
    item = {
        "id": 7820,
        "precio": 79000,
        "dolar": 1,
        "coordenadas": "-32.9504166, -68.8529303",
        "localidad": "Godoy Cruz",
        "provincia": "Mendoza",
        "direccion": "EDIFICIO ALFARO Piso 2 Depto 7",
        "slug": "departamentos-en-venta-en-edificio-alfaro-piso-2-depto-7",
        "descripcion": "Lindo depto",
        "fechaPublicacion": "2026-07-01 22:12:56",
        "inmobiliaria": {"id": 39165, "nombre": "INMOBILIARIA VILCHES", "slug": "inmobiliaria-vilches"},
        "servicios": [{"desc": "Dormitorios", "val": "2"}],
    }
    cards = api_items_to_cards([item])
    assert len(cards) == 1
    card = cards[0]
    assert card["url"] == (
        "https://inmoup.com.ar/39165-inmobiliaria-vilches/inmuebles/7820/"
        "ficha/departamentos-en-venta-en-edificio-alfaro-piso-2-depto-7"
    )
    assert card["description"] == "Lindo depto"
    assert card["datePosted"] == "2026-07-01 22:12:56"
    assert card["id"] == 7820
    assert card["precio"] == 79000


def test_card_url_id_prefixes_agency_slug():
    """Detail URLs must use the agency's id-prefixed slug (bare slug 404s)."""
    from property_hunter.ingest.extract import _card_url

    item = {
        "id": 7820,
        "slug": "departamento-x",
        "inmobiliaria": {"id": 39165, "slug": "inmobiliaria-vilches"},
    }
    assert _card_url(item) == (
        "https://inmoup.com.ar/39165-inmobiliaria-vilches/inmuebles/7820/ficha/departamento-x"
    )
    # already id-prefixed slugs are not double-prefixed
    already = {"id": 7820, "slug": "departamento-x", "inmobiliaria": {"id": 7, "slug": "7-agencia"}}
    assert _card_url(already) == "https://inmoup.com.ar/7-agencia/inmuebles/7820/ficha/departamento-x"
    # no agency id: falls back to the generic path like before
    bare = {"id": 7820, "slug": "departamento-x", "inmobiliaria": {"slug": "agencia"}}
    assert _card_url(bare) == "https://inmoup.com.ar/agencia/inmuebles/7820/ficha/departamento-x"
