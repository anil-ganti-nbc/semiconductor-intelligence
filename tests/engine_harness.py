"""Shared engine test harness (PLUGIN_GUIDE.md — now real, per DESIGN_REVIEW §0).

A new engine's test file needs ~20 lines: build the engine, point the
harness at routed fixture responses and a goldens directory, call the four
checks. Regenerate goldens deliberately with UPDATE_GOLDENS=1 pytest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from oem_radar.core.models import FetchedDocument, NormalizedProduct


class RouteFetcher:
    """Fetcher fake: exact URL → body. KeyError = the engine requested
    something the fixture set didn't anticipate (that's a finding)."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str) -> FetchedDocument:
        self.calls.append(url)
        return FetchedDocument(url=url, status=200, body=self.routes[url])


class EngineHarness:
    def __init__(self, engine, routes: dict[str, str], goldens_dir: Path):
        self.engine = engine
        self.fetcher = RouteFetcher(routes)
        self.goldens_dir = goldens_dir

    def discover(self):
        return list(self.engine.discover(self.fetcher))

    def normalize_all(self) -> dict[str, NormalizedProduct]:
        out = {}
        for ref in self.discover():
            if ref.inline_payload is not None:
                doc = FetchedDocument(url=ref.url, status=200,
                                      body=json.dumps(ref.inline_payload))
            else:
                doc = self.fetcher.get(ref.url)
            out[ref.handle or ref.url] = self.engine.normalize(self.engine.parse(doc))
        return out

    def assert_goldens(self, products: dict[str, NormalizedProduct]) -> None:
        """Canonical JSON of every product must match its stored golden.
        A mismatch means engine output changed: review the diff, then
        UPDATE_GOLDENS=1 pytest to accept it knowingly."""
        update = os.environ.get("UPDATE_GOLDENS") == "1"
        self.goldens_dir.mkdir(parents=True, exist_ok=True)
        for handle, product in products.items():
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in handle)[:80]
            path = self.goldens_dir / f"{safe}.json"
            current = product.canonical_json()
            if update or not path.exists():
                path.write_text(current, encoding="utf-8")
                continue
            golden = path.read_text(encoding="utf-8")
            assert current == golden, (
                f"engine output changed for {handle!r} vs golden {path.name}. "
                "If intentional: UPDATE_GOLDENS=1 pytest"
            )

    def assert_validate_flags(self, product: NormalizedProduct, field: str) -> None:
        issues = self.engine.validate(product)
        assert any(i.field == field for i in issues), \
            f"expected validation issue on {field!r}, got {issues}"

    def assert_config_rejected(self, bad_config: dict) -> None:
        try:
            self.engine.config_schema.model_validate(bad_config)
        except Exception:
            return
        raise AssertionError(f"config schema accepted invalid config: {bad_config}")
