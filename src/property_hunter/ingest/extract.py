"""JSON-LD + Next.js RSC extraction from inmoup.com.ar pages (research §1).

List pages carry two parallel data sources:

- A Next.js React Server Component payload (``self.__next_f.push([1, ...])``)
  whose ``initialItems`` array is the rendered card data: exact map-marker
  coordinates (``coordenadas``), the property's own ``localidad``/``provincia``,
  street address (``direccion``), USD price (``precio``), and ``servicios``.
  This is the authoritative per-listing source used by the pipeline.
- SEO ``ItemList``/``RealEstateListing`` JSON-LD blocks, which add the long
  description and ``datePosted`` (and whose ``provider.address`` is the real
  estate *agency's* address — never the property's location).

Detail pages embed a single ``Accommodation``/``RealEstateListing`` JSON-LD node
with the property's own ``geo`` coordinates and ``address.addressLocality``.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("property_hunter.ingest.extract")

_LD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_RSC_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")]\)', re.S)


def _load_blocks(html: str) -> list:
    blocks: list = []
    for raw in _LD_RE.findall(html):
        try:
            blocks.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            logger.debug("skipping unparseable ld+json block")
    return blocks


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_dicts(value)


def _has_type(node: dict, type_name: str) -> bool:
    t = node.get("@type")
    if isinstance(t, str):
        return t == type_name
    if isinstance(t, list):
        return type_name in t
    return False


def parse_ld_list_page(html: str) -> list[dict]:
    """Return the raw ``RealEstateListing`` item dicts from a list page's JSON-LD."""
    items: list[dict] = []
    for block in _load_blocks(html):
        for node in _iter_dicts(block):
            if not _has_type(node, "ItemList"):
                continue
            for element in node.get("itemListElement", []):
                if not isinstance(element, dict):
                    continue
                item = element.get("item")
                if isinstance(item, dict) and _has_type(item, "RealEstateListing"):
                    items.append(item)
    return items


def _rsc_text(html: str) -> str:
    """Concatenated, unescaped RSC flight-stream text from ``__next_f.push`` chunks."""
    buf: list[str] = []
    for raw in _RSC_PUSH_RE.findall(html):
        try:
            buf.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            logger.debug("skipping unparseable RSC push chunk")
    return "".join(buf)


def parse_rsc_list_payload(html: str) -> list[dict]:
    """Return the ``initialItems`` card data from the page's RSC payload.

    The RSC flight stream is split into ``self.__next_f.push([1, "..."])``
    string chunks; the ``initialItems`` array (the rendered listing cards) is a
    JSON array embedded in the concatenated, unescaped stream.
    """
    text = _rsc_text(html)
    marker = text.find('"initialItems"')
    if marker < 0:
        return []
    rest = text[marker + len('"initialItems"'):]
    start = rest.find("[")
    if start < 0:
        return []
    depth = 0
    end: int | None = None
    for i in range(start, len(rest)):
        if rest[i] == "[":
            depth += 1
        elif rest[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return []
    try:
        arr = json.loads(rest[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.debug("initialItems array did not parse")
        return []
    return [item for item in arr if isinstance(item, dict)]


_RSC_FILTERS_RE = re.compile(r'"filters":\s*(\{[^{}]*\})')


def parse_rsc_filters(html: str) -> dict | None:
    """Return the list page's embedded search-filter state (``filters`` object).

    The server-rendered page embeds the active search state as a JSON object,
    e.g. ``{"grupo":2,"condiciones":1,"provincias":[1]}`` (research §2). This is
    exactly the ``filtros`` body the site's own JSON API accepts, so we can
    reuse it to paginate via the API instead of the (broken) ``?pagina=N`` SSR
    pages, which always re-render the first page of items.
    """
    text = _rsc_text(html)
    for match in _RSC_FILTERS_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and "grupo" in payload and "condiciones" in payload:
            return payload
    return None


def _card_url(item: dict) -> str:
    """Canonical detail-page URL for a card item.

    The working detail URLs use the agency's id-prefixed slug,
    ``{agency_id}-{agency_slug}/inmuebles/{id}/ficha/{slug}`` (a bare agency
    slug 404s; research §1/§3). Guard against a source that already prefixes it.
    """
    listing_id = item.get("id")
    agency = item.get("inmobiliaria") if isinstance(item.get("inmobiliaria"), dict) else {}
    agency_id = agency.get("id")
    agency_slug = agency.get("slug") or "inmuebles"
    if agency_id and agency_slug != "inmuebles" and not agency_slug.startswith(f"{agency_id}-"):
        agency_slug = f"{agency_id}-{agency_slug}"
    address_slug = item.get("slug") or ""
    return f"https://inmoup.com.ar/{agency_slug}/inmuebles/{listing_id}/ficha/{address_slug}"


def api_items_to_cards(items: list[dict]) -> list[dict]:
    """Map ``/server/inmuebles/buscar`` items to the canonical list-page shape.

    The API returns the same per-listing fields as the RSC ``initialItems``
    cards plus ``descripcion``/``fechaPublicacion``; add the canonical
    ``url``/``description``/``datePosted`` so ``normalize_listing`` can treat
    them exactly like SSR cards (research §2).
    """
    cards: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        card = dict(item)
        card["url"] = _card_url(item)
        card["description"] = item.get("descripcion")
        card["datePosted"] = item.get("fechaPublicacion")
        cards.append(card)
    return cards


def _ld_id(item: dict) -> int | None:
    from property_hunter.normalize import parse_source_listing_id

    return parse_source_listing_id(str(item.get("url") or ""))


def parse_list_page(html: str) -> list[dict]:
    """Return canonical list-page items: RSC card data + JSON-LD enrichment.

    RSC ``initialItems`` is authoritative (exact coordinates, property
    ``localidad``, address, price, attributes). The SEO JSON-LD blocks add the
    long ``description`` and ``datePosted``, matched by listing id. Falls back
    to JSON-LD items alone when the page has no RSC payload.
    """
    rsc_items = parse_rsc_list_payload(html)
    if not rsc_items:
        return parse_ld_list_page(html)

    ld_by_id: dict[int, dict] = {}
    for item in parse_ld_list_page(html):
        lid = _ld_id(item)
        if lid:
            ld_by_id[lid] = item

    canonical: list[dict] = []
    for item in rsc_items:
        listing_id = item.get("id")
        ld = ld_by_id.get(listing_id, {}) if isinstance(listing_id, int) else {}
        merged = dict(item)
        if not merged.get("description"):
            merged["description"] = ld.get("description")
        if not merged.get("datePosted"):
            merged["datePosted"] = ld.get("datePosted") or item.get("fechaPublicacion")
        url = ld.get("url") or item.get("url")
        if not url:
            url = _card_url(item)
        merged["url"] = url
        canonical.append(merged)
    return canonical


def parse_detail_page(html: str) -> dict | None:
    """Return the detail listing node, or ``None`` if the page has none."""
    for block in _load_blocks(html):
        for node in _iter_dicts(block):
            if (
                _has_type(node, "RealEstateListing")
                and isinstance(node.get("offers"), dict)
                and isinstance(node.get("address"), dict)
                and node.get("@id")
            ):
                return node
    return None
