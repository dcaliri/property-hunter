"""JSON-LD + HTML extraction from inmoup.com.ar pages (research §1).

List pages embed an ``ItemList`` of ``RealEstateListing`` items; detail pages
embed a single ``Accommodation``/``RealEstateListing`` node.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("property_hunter.ingest.extract")

_LD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


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


def parse_list_page(html: str) -> list[dict]:
    """Return the raw ``RealEstateListing`` item dicts from a list page."""
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
