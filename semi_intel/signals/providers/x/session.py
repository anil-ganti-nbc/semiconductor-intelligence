"""Persistent authenticated Playwright browser session for X collection.

Playwright remains optional and is imported only when a browser session is
started.  Authentication cookies come from the operator-imported session
file; this module never stores credentials itself.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


def configure_frozen_browser_cache() -> Path | None:
    """Point a frozen build at Playwright's normal per-user browser cache.

    Playwright defaults frozen applications to a package-local browser folder.
    Our executable carries the driver but deliberately does not embed Chromium,
    so reuse the browser installed by ``playwright install chromium``. An
    operator-supplied PLAYWRIGHT_BROWSERS_PATH always wins.
    """
    if not getattr(sys, "frozen", False) or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return None

    if sys.platform == "win32":
        cache_root = os.environ.get("LOCALAPPDATA")
        candidate = Path(cache_root) / "ms-playwright" if cache_root else None
    elif sys.platform == "darwin":
        candidate = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        candidate = Path.home() / ".cache" / "ms-playwright"

    if candidate is not None and candidate.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
        return candidate
    return None


class SessionExpired(RuntimeError):
    pass


class BrowserSession:
    def __init__(
        self,
        profile_dir: str,
        headless: bool = True,
        session_file: str | None = None,
    ):
        self.profile_dir = profile_dir
        self.headless = headless
        self.session_file = session_file or str(
            Path(profile_dir).parent / "x_session.json"
        )
        self._pw = None
        self._context = None

    async def start(self):
        configure_frozen_browser_cache()
        from playwright.async_api import async_playwright

        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            self.profile_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 2000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
        )
        if Path(self.session_file).exists():
            from semi_intel.signals.providers.x.auth import load_session

            cookies = load_session(self.session_file)
            if cookies:
                await self._context.add_cookies(cookies)
        return self._context

    async def new_page(self):
        if self._context is None:
            await self.start()
        return await self._context.new_page()

    async def is_authenticated(self) -> bool:
        """Load X home and detect an authentication redirect."""
        page = await self.new_page()
        try:
            await page.goto(
                "https://x.com/home",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            return "/login" not in page.url and "/i/flow/login" not in page.url
        finally:
            await page.close()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
