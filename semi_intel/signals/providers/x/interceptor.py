"""Capture network responses, keep GraphQL timeline payloads.

Ported from Signal Radar. Subscribes to a Playwright page's `response`
event, buffers JSON bodies from GraphQL operations that carry user
timelines, and exposes them for the collector to walk. Knows about
transport, not meaning -- see normalizer.py for the schema-aware code.
"""

from __future__ import annotations

from typing import Any

# GraphQL operation names that return a user's tweets. X renames these over
# time; keeping them in one list makes drift a one-line fix.
TIMELINE_OPS = ("UserTweets", "UserTweetsAndReplies", "UserMedia")


class TimelineInterceptor:
    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    def attach(self, page) -> None:
        page.on("response", self._on_response)

    async def _on_response(self, response) -> None:
        url = response.url
        if "/graphql/" not in url:
            return
        if not any(op in url for op in TIMELINE_OPS):
            return
        try:
            data = await response.json()
        except Exception:
            return
        self.payloads.append(data)

    def drain(self) -> list[dict[str, Any]]:
        out = self.payloads
        self.payloads = []
        return out


def iter_tweet_results(graphql_payload: dict[str, Any]):
    """Walk a GraphQL timeline payload and yield raw tweet 'result' objects.

    The structure is deeply nested (data -> user -> result -> timeline ->
    instructions -> entries -> content -> itemContent -> tweet_results ->
    result). We walk defensively and yield anything that looks like a tweet
    result, so minor shape changes above the tweet object don't break
    extraction.
    """
    def walk(node):
        if isinstance(node, dict):
            tr = node.get("tweet_results")
            if isinstance(tr, dict) and "result" in tr:
                yield tr["result"]
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    yield from walk(graphql_payload)
