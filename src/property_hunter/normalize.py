"""Canonicalization, identity keying, and validation (constitution III)."""

from __future__ import annotations

import logging
import re
from typing import Any

from property_hunter.models import ListingRecord, Operation, PropertyType

logger = logging.getLogger("property_hunter.normalize")

LISTING_ID_RE = re.compile(r"/inmuebles/(\d+)/")

OPERATION_MAP: dict[str, Operation] = {
    "venta": "sale",
    "renta": "rent",
    "alquiler": "rent",
    "alquileres": "rent",
    "arriendo": "rent",
}

TYPE_MAP: dict[str, PropertyType] = {
    "departamentos": "departamento",
    "departamento": "departamento",
    "casas": "casa",
    "casa": "casa",
    "ph": "ph",
    "lotes": "lote",
    "terrenos": "lote",
    "oficinas": "oficina",
    "locales": "local",
    "local": "local",
    "cocheras": "otro",
    "cochera": "otro",
}

_NON_BARRIO_WORDS = {"venta", "renta", "alquiler", "alquileres"}


def parse_source_listing_id(url: str) -> int | None:
    match = LISTING_ID_RE.search(url or "")
    if not match:
        return None
    return int(match.group(1))


def operation_from_slug(slug: str) -> Operation:
    return OPERATION_MAP.get((slug or "").lower(), "sale")


def property_type_from_slug(slug: str) -> PropertyType:
    return TYPE_MAP.get((slug or "").lower(), "otro")


def barrio_from_name(name: str) -> str | None:
    """Parse the barrio from a list-page item name, e.g. ``... en Palermo, 1 dormitorios``.

    Last-resort fallback only; location decisions never come from the real
    estate agency's address (see ``_get_address_field``).
    """
    first = (name or "").split(",")[0].strip()
    parts = [p.strip() for p in first.split(" en ") if p.strip()]
    if not parts:
        return None
    candidate = parts[-1]
    if candidate.lower() in _NON_BARRIO_WORDS:
        return None
    return candidate or None


def parse_coordinates(value: Any) -> tuple[float | None, float | None]:
    """Parse ``"lat, lng"`` into a (lat, lng) tuple, or ``(None, None)``."""
    if value is None:
        return None, None
    parts = [p.strip() for p in str(value).split(",")]
    if len(parts) != 2:
        return None, None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None
    return lat, lng


def _get_address_field(item: dict, *keys: str) -> str | None:
    """Look up a nested field on the *property's* address.

    Deliberately reads ``item.address`` only — never ``provider.address``, which
    is the real estate agency's office location, not the property's (research §1).
    """
    addr = item.get("address")
    if not isinstance(addr, dict):
        return None
    for key in keys:
        value = addr.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _provider_region(item: dict) -> str:
    """Region fallback from the agency address (same province as the property)."""
    provider = item.get("provider")
    if isinstance(provider, dict):
        addr = provider.get("address")
        if isinstance(addr, dict):
            value = addr.get("addressRegion")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    num = _number(value)
    return int(num) if num is not None else None


def _servicios_map(item: dict) -> dict[str, str]:
    """RSC ``servicios`` list -> {description: value}."""
    props: dict[str, str] = {}
    for prop in item.get("servicios", []):
        if isinstance(prop, dict) and prop.get("desc"):
            value = prop.get("val")
            props[str(prop["desc"])] = value if value is not None else ""
    return props


def _normalize_rsc(
    item: dict,
    operation: Operation,
    property_type: PropertyType,
    observed_at: str,
    source_url: str = "",
) -> ListingRecord:
    """Normalize a Next.js RSC ``initialItems`` card (list pages, research §1).

    Location comes from the exact map-marker coordinates (``coordenadas``) and
    the property's own ``localidad`` — never the agency's.
    """
    url = item.get("url") or source_url or ""
    listing_id = item.get("id")
    if not isinstance(listing_id, int):
        listing_id = parse_source_listing_id(url) or 0

    price_value = _number(item.get("precio"))
    price_cents = int(round(price_value * 100)) if price_value is not None else 0
    currency = "ARS" if item.get("dolar") == 0 else "USD"

    lat, lng = parse_coordinates(item.get("coordenadas"))
    servs = _servicios_map(item)
    provider = item.get("inmobiliaria") if isinstance(item.get("inmobiliaria"), dict) else {}

    return ListingRecord(
        source="inmoup",
        source_listing_id=listing_id,
        source_url=url,
        operation=operation,
        property_type=property_type,
        street_address=(item.get("direccion") or "").strip() or None,
        barrio=(item.get("localidad") or "unknown").strip(),
        region=(item.get("provincia") or "").strip(),
        lat=lat,
        lng=lng,
        beds=_int(servs.get("Dormitorios")),
        baths=_int(servs.get("Baños")),
        covered_area_m2=_number(servs.get("Superficie Cubierta m2")),
        total_area_m2=_number(servs.get("Superficie Total m2")),
        agency_name=provider.get("nombre"),
        description=item.get("description"),
        date_posted=item.get("datePosted"),
        price_cents=price_cents,
        currency=currency if currency in ("USD", "ARS") else "USD",
        raw_price_text=str(item.get("precio")) if item.get("precio") is not None else None,
        observed_at=observed_at,
    )


def _normalize_ld(
    item: dict,
    operation: Operation,
    property_type: PropertyType,
    observed_at: str,
    source_url: str = "",
) -> ListingRecord:
    """Normalize a JSON-LD ``RealEstateListing`` node (detail pages, research §1).

    ``barrio``/coordinates come from the property's own address/geo — never the
    agency's (``provider.address``). Region falls back to the agency only.
    """
    url = item.get("url") or source_url or ""
    listing_id = parse_source_listing_id(url)

    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    price_raw = offers.get("price")
    price_cents = int(round(_number(price_raw) * 100)) if _number(price_raw) is not None else 0
    currency = offers.get("priceCurrency") or "USD"

    region = _get_address_field(item, "addressRegion") or _provider_region(item) or ""
    barrio = _get_address_field(item, "addressLocality") or barrio_from_name(item.get("name")) or "unknown"

    geo = item.get("geo") if isinstance(item.get("geo"), dict) else {}
    lat = _number(geo.get("latitude"))
    lng = _number(geo.get("longitude"))

    props: dict[str, str] = {}
    for prop in item.get("additionalProperty", []):
        if isinstance(prop, dict) and prop.get("name"):
            value = prop.get("value")
            props[str(prop["name"])] = value if value is not None else ""

    beds = _int(props.get("Dormitorios"))
    baths = _int(props.get("Baños"))
    covered_area_m2 = _number(props.get("Superficie") or props.get("Superficie Cubierta m2"))
    total_area_m2 = _number(props.get("Superficie Total m2"))

    provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
    agency = provider.get("name") if isinstance(provider, dict) else None

    street_address = None
    address = item.get("address")
    if isinstance(address, dict) and isinstance(address.get("streetAddress"), str):
        street_address = address.get("streetAddress")

    return ListingRecord(
        source="inmoup",
        source_listing_id=listing_id or 0,
        source_url=url,
        operation=operation,
        property_type=property_type,
        street_address=street_address,
        barrio=barrio,
        region=region,
        lat=lat,
        lng=lng,
        beds=beds,
        baths=baths,
        covered_area_m2=covered_area_m2,
        total_area_m2=total_area_m2,
        agency_name=agency,
        description=item.get("description"),
        date_posted=item.get("datePosted"),
        price_cents=price_cents,
        currency=currency if currency in ("USD", "ARS") else "USD",
        raw_price_text=str(price_raw) if price_raw is not None else None,
        observed_at=observed_at,
    )


def normalize_listing(
    item: dict,
    operation: Operation,
    property_type: PropertyType,
    observed_at: str,
    source_url: str = "",
) -> ListingRecord:
    """Dispatch on source shape: RSC card data (list pages) vs JSON-LD (details)."""
    if isinstance(item.get("id"), int) and "precio" in item:
        return _normalize_rsc(item, operation, property_type, observed_at, source_url)
    return _normalize_ld(item, operation, property_type, observed_at, source_url)
