"""XProvider -- assembles the layers behind the synchronous Provider contract.

Ported from Signal Radar's async `XProvider` and adapted to this codebase's
synchronous provider protocol (semi_intel has no asyncio runtime elsewhere;
see semi_intel/signals/providers/__init__.py) by wrapping each async call
with `asyncio.run()`. Internally unchanged: BrowserSession -> interceptor ->
collect_profile -> normalizer, with best-effort HTML fallback and explicit
degradation (never crashes the caller).

Every entry point that needs Playwright or a session raises
`ProviderUnavailable` on any of:
  - the `playwright` package not being installed (the `x` extra);
  - no imported session file at `session_file_path()`;
  - a session that no longer authenticates (expired/logged out).

`CollectionService` (semi_intel/signals/collection.py) treats
ProviderUnavailable exactly like Signal Radar treats a missing X session:
the source is skipped for this cycle, not fatally errored.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from semi_intel.signals.providers import (
    CollectResult,
    Cursor,
    NormalizedSignal,
    ProviderUnavailable,
    RawItem,
    SourceCandidate,
    ValidationError,
)


from semi_intel.paths import get_x_session_path, get_x_profile_dir

def session_file_path() -> str:
    """Where an imported X session lives. Never inside the package, never
    packaged into the distributable, overridable per-operator."""
    return os.environ.get("SEMI_INTEL_X_SESSION_PATH", str(get_x_session_path()))


def profile_dir_path() -> str:
    return os.environ.get("SEMI_INTEL_X_PROFILE_DIR", str(get_x_profile_dir()))


def _require_playwright() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise ProviderUnavailable(
            "Playwright is not installed. X collection is an optional extra: "
            "install with `pip install semi-intel[x]` and `playwright install chromium`."
        ) from exc


class XProvider:
    name = "x"

    def __init__(self, profile_dir: str | None = None, headless: bool = True):
        _require_playwright()
        from semi_intel.signals.providers.x.session import BrowserSession

        self.profile_dir = profile_dir or profile_dir_path()
        self.session = BrowserSession(self.profile_dir, headless=headless, session_file=session_file_path())
        self.last_collection_path = "graphql"

    def collect(self, source_handle: str, cursor: Cursor | None) -> CollectResult:
        if not Path(self.session.session_file).exists():
            raise ProviderUnavailable(
                f"No X session imported at {self.session.session_file!r}. "
                "Run `semi-intel radar connect-x` first (see docs/X_PROVIDER.md)."
            )
        return asyncio.run(self._collect_async(source_handle, cursor))

    async def _collect_async(self, source_handle: str, cursor: Cursor | None) -> CollectResult:
        from semi_intel.signals.providers.x import html_fallback
        from semi_intel.signals.providers.x.collector import collect_profile
        from semi_intel.signals.providers.x.session import SessionExpired

        try:
            await self.session.start()
            if not await self.session.is_authenticated():
                raise SessionExpired("X session not authenticated -- re-run `radar connect-x`")

            try:
                items, newest = await collect_profile(self.session, source_handle, cursor)
                self.last_collection_path = "graphql"
                if not items and cursor is None:
                    raise RuntimeError("no timeline payloads intercepted")
                return CollectResult(items=items, next_cursor=Cursor(newest) if newest else cursor)
            except SessionExpired:
                raise
            except Exception:
                items, newest = await html_fallback.collect(self.session, source_handle, cursor)
                self.last_collection_path = "html_fallback"
                return CollectResult(items=items, next_cursor=Cursor(newest) if newest else cursor)
        except SessionExpired as exc:
            raise ProviderUnavailable(str(exc)) from exc
        finally:
            await self.session.close()

    def normalize(self, raw: RawItem) -> NormalizedSignal:
        from semi_intel.signals.providers.x import html_fallback
        from semi_intel.signals.providers.x.normalizer import SchemaDrift, normalize

        if raw.payload.get("_fidelity") == "low":
            return html_fallback.normalize(raw)
        try:
            return normalize(raw)
        except SchemaDrift:
            # Surfaced as a processing_error by the caller; degrade to a
            # minimal record rather than losing the item entirely.
            p = raw.payload
            return NormalizedSignal(
                external_id=str(raw.external_id), provider=self.name,
                author_handle=p.get("_author", ""), posted_at=None,
                text=p.get("_text", ""), fidelity="low_fidelity", raw=p,
            )

    def validate(self, handle_or_url: str) -> SourceCandidate | ValidationError:
        handle = handle_or_url.strip().rstrip("/").split("/")[-1].lstrip("@")
        if not handle or " " in handle:
            return ValidationError("invalid handle")
        # A full validation would load the profile and confirm it exists; kept
        # light so Add Source stays responsive. The first collection run
        # confirms reality (and requires a session -- see collect()).
        return SourceCandidate(provider="x", provider_key=handle, display_name=handle,
                               profile_url=f"https://x.com/{handle}")
