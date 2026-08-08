"""Best-effort DOM extraction when GraphQL interception fails.

Ported from Signal Radar. Lower fidelity than GraphQL -- typically text,
author, timestamp, and status id from article elements, missing
quoted-post structure. Results are tagged low-fidelity (SignalItem.fidelity)
so downstream (and the operator) know. This keeps collection working during
frontend changes; it is not equivalent to the primary path.
"""

from __future__ import annotations

from datetime import datetime

from semi_intel.signals.providers import Cursor, NormalizedSignal, RawItem


async def collect(session, handle: str, cursor: Cursor | None,
                  max_scrolls: int = 6) -> tuple[list[RawItem], str | None]:
    page = await session.new_page()
    stop_id = cursor.value if cursor else None
    items: dict[str, dict] = {}
    try:
        await page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded",
                        timeout=45000)
        for _ in range(max_scrolls):
            await page.wait_for_timeout(1500)
            articles = await page.query_selector_all("article[data-testid='tweet']")
            for art in articles:
                link = await art.query_selector("a[href*='/status/']")
                if not link:
                    continue
                href = await link.get_attribute("href") or ""
                rid = href.rstrip("/").split("/status/")[-1].split("?")[0]
                if not rid.isdigit() or rid in items:
                    continue
                if stop_id and rid == stop_id:
                    continue
                text_el = await art.query_selector("div[data-testid='tweetText']")
                text = (await text_el.inner_text()) if text_el else ""
                time_el = await art.query_selector("time")
                ts = (await time_el.get_attribute("datetime")) if time_el else None
                items[rid] = {"_fidelity": "low", "_text": text, "_author": handle,
                              "_posted_at": ts, "external_id": rid}
            await page.mouse.wheel(0, 3000)
        newest = max(items, key=int) if items else stop_id
        ordered = sorted(items, key=int)
        return [RawItem(external_id=i, payload=items[i]) for i in ordered], newest
    finally:
        await page.close()


def normalize(raw: RawItem) -> NormalizedSignal:
    p = raw.payload
    ts = p.get("_posted_at")
    posted_at = None
    if ts:
        try:
            posted_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            posted_at = None
    return NormalizedSignal(
        external_id=str(p["external_id"]), provider="x",
        author_handle=p.get("_author", ""), posted_at=posted_at,
        text=p.get("_text", ""), fidelity="low_fidelity", raw=p,
    )
