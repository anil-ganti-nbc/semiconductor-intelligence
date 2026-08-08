"""Navigate a profile, drive the interceptor, walk newest -> oldest.

Ported from Signal Radar. Given an authenticated session, this loads a
source's profile page, scrolls to trigger the timeline GraphQL calls the
interceptor captures, extracts tweet result objects, and stops as soon as it
reaches the stored cursor (incremental collection). Emits RawItems in
chronological order for the provider to persist.
"""

from __future__ import annotations

import asyncio
import random

from semi_intel.signals.providers import Cursor, RawItem
from semi_intel.signals.providers.x.interceptor import TimelineInterceptor, iter_tweet_results


def _rest_id(result: dict) -> str | None:
    r = result.get("tweet", result)
    rid = r.get("rest_id") or (r.get("legacy", {}) or {}).get("id_str")
    return str(rid) if rid else None


async def collect_profile(session, handle: str, cursor: Cursor | None,
                          max_scrolls: int = 8) -> tuple[list[RawItem], str | None]:
    """Returns (items_chronological, newest_id). Stops early at the cursor id."""
    interceptor = TimelineInterceptor()
    page = await session.new_page()
    interceptor.attach(page)
    seen_ids: list[str] = []
    raw_by_id: dict[str, dict] = {}
    stop_id = cursor.value if cursor else None
    reached_stop = False
    try:
        await page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded",
                        timeout=45000)
        for _ in range(max_scrolls):
            await asyncio.sleep(random.uniform(1.0, 2.5))  # human dwell
            for payload in interceptor.drain():
                for result in iter_tweet_results(payload):
                    rid = _rest_id(result)
                    if not rid or rid in raw_by_id:
                        continue
                    if stop_id is not None and rid == stop_id:
                        reached_stop = True
                        continue
                    raw_by_id[rid] = result
                    seen_ids.append(rid)
            if reached_stop:
                break
            await page.mouse.wheel(0, 3000)
        # newest id = numerically-largest tweet id we saw (X ids are monotonic)
        newest = max(raw_by_id, key=int) if raw_by_id else stop_id
        ordered = sorted(raw_by_id, key=int)  # chronological order for storage
        items = [RawItem(external_id=i, payload=raw_by_id[i]) for i in ordered]
        return items, newest
    finally:
        await page.close()
