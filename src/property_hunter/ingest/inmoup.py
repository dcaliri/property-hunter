"""inmoup.com.ar search URL builder and page fetching."""

from __future__ import annotations

from property_hunter.ingest.client import PoliteClient

BASE_URL = "https://inmoup.com.ar"
SEARCH_ENDPOINT = BASE_URL + "/server/inmuebles/buscar"

LIMIT = 24


def build_search_url(operation_slug: str, type_slug: str, region_slug: str, page: int = 1) -> str:
    """Build a list-page URL. ``page`` > 1 adds ``?pagina=N``."""
    slug = f"{type_slug}-en-{operation_slug}-en-{region_slug}"
    url = f"{BASE_URL}/{slug}"
    if page and page > 1:
        url = f"{url}?pagina={page}"
    return url


class OfflineReader:
    """Serves saved fixture pages for ``--offline-fixtures`` runs (no network)."""

    def __init__(self, fixtures: list[bytes]):
        self._fixtures = fixtures

    def get(self, url: str) -> tuple[int, bytes]:
        page_num = _page_number(url)
        idx = page_num - 1
        if idx >= len(self._fixtures):
            return 200, b""
        return 200, self._fixtures[idx]


def _page_number(url: str) -> int:
    import urllib.parse

    qs = urllib.parse.urlparse(url).query
    params = urllib.parse.parse_qs(qs)
    try:
        return int(params.get("pagina", ["1"])[0])
    except ValueError:
        return 1


def make_fetcher(config, offline: bool = False, fixtures: list[bytes] | None = None):
    """Return ``(fetch, search)``.

    ``fetch`` is ``(url) -> (status, body)`` for SSR pages; ``search`` is
    ``(filters, page, limit) -> (status, body)`` for the site's JSON API
    (``POST /server/inmuebles/buscar``). ``search`` is ``None`` offline.
    """
    if offline:
        reader = OfflineReader(fixtures or [])
        return reader.get, None
    client = PoliteClient(config)
    return client.get, client.post_json


def search_payload(filters: dict, page: int, limit: int = LIMIT) -> dict:
    """Build the request body for the site's search API (research §2)."""
    return {"filtros": filters, "page": page, "limit": limit}
