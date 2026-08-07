"""Politeness-limited HTTP client (constitution II).

Throttles requests with a configurable delay, uses an identifiable user-agent,
and retries with exponential backoff on 429/5xx responses.
"""

from __future__ import annotations

import logging
import time

import httpx

from property_hunter.config import CollectConfig

logger = logging.getLogger("property_hunter.ingest.client")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class PoliteClient:
    def __init__(self, config: CollectConfig):
        self.config = config
        self._last_request_at = 0.0
        self._client = httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": config.user_agent},
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.config.delay_seconds - elapsed
        if wait > 0:
            logger.debug("politeness delay", extra={"ctx_delay_ms": int(wait * 1000)})
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _request(self, method: str, url: str, *, json_body: dict | None = None,
                 headers: dict | None = None) -> tuple[int, bytes]:
        last_status = 0
        last_body = b""
        kwargs: dict = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if headers:
            kwargs["headers"] = headers
        for attempt in range(1, self.config.max_retries + 1):
            self._throttle()
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                logger.warning("request failed (attempt %d/%d)", attempt, self.config.max_retries,
                               extra={"ctx_url": url, "ctx_error": str(exc)})
                if attempt == self.config.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 30))
                continue
            last_status = resp.status_code
            last_body = resp.content
            if resp.status_code == 200:
                return resp.status_code, resp.content
            if resp.status_code in RETRYABLE_STATUS and attempt < self.config.max_retries:
                logger.warning("retryable status %d (attempt %d/%d)", resp.status_code,
                               attempt, self.config.max_retries, extra={"ctx_url": url})
                time.sleep(min(2 ** attempt, 30))
                continue
            return resp.status_code, resp.content
        return last_status, last_body

    def get(self, url: str) -> tuple[int, bytes]:
        return self._request("GET", url)

    def post_json(self, url: str, payload: dict) -> tuple[int, bytes]:
        """POST JSON to the site's client API (inmoup search endpoint).

        The site's Next.js API requires ``Origin``/``Referer`` headers and
        rejects requests without them (research §2).
        """
        return self._request(
            "POST", url, json_body=payload,
            headers={"Origin": "https://inmoup.com.ar", "Referer": "https://inmoup.com.ar/"})

    def close(self) -> None:
        self._client.close()
