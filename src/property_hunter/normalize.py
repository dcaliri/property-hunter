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
    """Parse the barrio from a list-page item name, e.g. ``... en Palermo, 1 dormitorios``."""
    first = (name or "").split(",")[0].strip()
    parts = [p.strip() for p in first.split(" en ") if p.strip()]
    if not parts:
        return None
    candidate = parts[-1]
    if candidate.lower() in _NON_BARRIO_WORDS:
        return None
    return candidate or None


def _get_address_field(item: dict, *keys: str) -> str | None:
    """Look up a nested field across item.address and provider.address."""
    for container_key in ("address", "provider"):
        container = item.get(container_key) if isinstance(item.get(container_key), dict) else {}
        if isinstance(container, dict):
            addr = container.get("address")
            if isinstance(addr, dict):
                for key in keys:
                    value = addr.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    return None


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


def normalize_listing(
    item: dict,
    operation: Operation,
    property_type: PropertyType,
    observed_at: str,
    source_url: str = "",
) -> ListingRecord:
    url = item.get("url") or source_url or ""
    listing_id = parse_source_listing_id(url)

    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    price_raw = offers.get("price")
    price_cents = int(round(_number(price_raw) * 100)) if _number(price_raw) is not None else 0
    currency = offers.get("priceCurrency") or "USD"

    region = _get_address_field(item, "addressRegion") or ""
    barrio = (
        _get_address_field(item, "addressLocality")
        or barrio_from_name(item.get("name"))
        or "unknown"
    )

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
