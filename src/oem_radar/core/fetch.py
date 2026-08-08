"""HttpFetcher (M1): the only component that touches the network.

Politeness is enforced here so engines can't get it wrong:
- per-domain jittered delays
- exponential backoff with cap on 429/5xx/connection errors
- conditional GETs (ETag / Last-Modified) with an on-disk cache
- descriptive User-Agent

Time and randomness are injectable for deterministic tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import requests

from .models import FetchedDocument

log = logging.getLogger("oem_radar.fetch")

DEFAULT_UA = "OEMRadar/0.1 (product-intelligence; respectful crawler; contact@x8.design)"

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, url: str, message: str, status: int | None = None):
        self.url, self.status = url, status
        super().__init__(f"{url}: {message}")


class HttpFetcher:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        user_agent: str = DEFAULT_UA,
        delay_range: tuple[float, float] = (3.0, 9.0),
        backoff_base: float = 2.0,
        backoff_max: float = 300.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.delay_range = delay_range
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sleep = sleep
        self.rng = rng or random.Random()
        self._last_request: dict[str, float] = {}  # domain -> monotonic ts
        self.stats = {"requests": 0, "cache_hits_304": 0, "retries": 0}

    # -- cache ---------------------------------------------------------------

    def _cache_paths(self, url: str) -> tuple[Path, Path] | None:
        if not self.cache_dir:
            return None
        # 24 hex chars ≈ 96 bits: collision-safe for cache purposes, and keeps
        # total paths short (Windows MAX_PATH is 260 and users may run from
        # deeply nested folders).
        h = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.meta.json", self.cache_dir / f"{h}.body"

    def _cache_read(self, url: str) -> dict | None:
        paths = self._cache_paths(url)
        if not paths or not paths[0].exists():
            return None
        try:
            meta = json.loads(paths[0].read_text(encoding="utf-8"))
            meta["body"] = paths[1].read_text(encoding="utf-8")
            return meta
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_write(self, url: str, resp: requests.Response) -> None:
        """Caching is an optimization; it must never fail a fetch (long paths,
        full disk, exotic filesystems — degrade to uncached, log, move on)."""
        paths = self._cache_paths(url)
        if not paths:
            return
        meta = {
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "content_type": resp.headers.get("Content-Type", "text/html"),
            "status": resp.status_code,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            paths[0].write_text(json.dumps(meta), encoding="utf-8")
            paths[1].write_text(resp.text, encoding="utf-8")
        except OSError as exc:
            log.warning("cache write failed for %s (%s) — continuing uncached", url, exc)

    # -- politeness ----------------------------------------------------------

    def _be_polite(self, domain: str) -> None:
        last = self._last_request.get(domain)
        if last is not None:
            delay = self.rng.uniform(*self.delay_range)
            elapsed = time.monotonic() - last
            if elapsed < delay:
                self.sleep(delay - elapsed)
        self._last_request[domain] = time.monotonic()

    # -- public --------------------------------------------------------------

    def get(self, url: str) -> FetchedDocument:
        domain = urlsplit(url).netloc
        cached = self._cache_read(url)
        headers = {"User-Agent": self.user_agent}
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]

        last_error: str = "unknown"
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                backoff = min(self.backoff_base**attempt, self.backoff_max)
                backoff *= self.rng.uniform(0.5, 1.5)  # jitter, avoid thundering herd
                self.stats["retries"] += 1
                log.info("retry %d for %s in %.1fs", attempt, url, backoff)
                self.sleep(backoff)
            self._be_polite(domain)
            self.stats["requests"] += 1
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
            except FileNotFoundError as exc:
                # requests raises this (not a RequestException) when the certifi
                # CA bundle is missing/broken. Retrying can't help — fail with a
                # fixable message instead of a cryptic errno.
                raise FetchError(
                    url,
                    f"TLS CA certificate bundle missing ({exc.filename or exc}). "
                    "Fix: pip install --force-reinstall certifi requests",
                ) from exc
            except (requests.RequestException, OSError) as exc:
                last_error = repr(exc)
                continue

            if resp.status_code == 304 and cached:
                self.stats["cache_hits_304"] += 1
                return FetchedDocument(
                    url=url, status=200, body=cached["body"],
                    content_type=cached.get("content_type", "text/html"), from_cache=True,
                )
            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    self.sleep(min(float(retry_after), self.backoff_max))
                continue
            if resp.status_code >= 400:
                raise FetchError(url, f"HTTP {resp.status_code}", resp.status_code)

            self._cache_write(url, resp)
            return FetchedDocument(
                url=url, status=resp.status_code, body=resp.text,
                content_type=resp.headers.get("Content-Type", "text/html"),
                headers={k: v for k, v in resp.headers.items()
                         if k.lower() in ("etag", "last-modified", "content-type")},
            )

        raise FetchError(url, f"exhausted {self.max_retries} retries ({last_error})")
