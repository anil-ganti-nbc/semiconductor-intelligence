from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from semi_intel.domain.enums import SourceSuggestionKind
from semi_intel.domain.models import SourceSuggestion
from semi_intel.web.app import create_app


@pytest.fixture()
def client():
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as client:
        yield client


def test_topic_crud_and_duplicate_protection(cli_env, client):
    api = client
    seeded = api.get("/api/topics").json()
    assert any(topic["name"] == "RDNA 5" for topic in seeded)

    created = api.post("/api/topics", json={
        "name": "Rubin Ultra", "aliases": ["Rubin-Ultra"],
        "category": "NVIDIA", "priority": .9, "enabled": True,
    })
    assert created.status_code == 201
    topic_id = created.json()["id"]
    assert created.json()["aliases"] == ["Rubin-Ultra"]

    duplicate = api.post("/api/topics", json={"name": "Rubin-Ultra", "aliases": []})
    assert duplicate.status_code == 409

    updated = api.put(f"/api/topics/{topic_id}", json={
        "name": "Rubin Ultra", "keyword": "Rubin Ultra", "aliases": ["RubinUltra"],
        "category": "NVIDIA", "priority": .8, "enabled": False,
    })
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert api.delete(f"/api/topics/{topic_id}").status_code == 204


def test_automatic_inbox_and_persistent_seen_state(cli_env, client):
    api = client
    source = api.post("/api/sources", json={
        "name": "Test Wire", "type": "manual", "trust_weight": .7,
    }).json()
    evidence = api.post("/api/evidence", json={
        "source_id": source["id"],
        "title": "New RDNA5 architecture details emerge",
        "content": "Fresh RDNA-5 information.",
        "url": "https://wire.example/rdna",
    })
    assert evidence.status_code == 201

    inbox = api.get("/api/editorial/stories").json()
    assert len(inbox) == 1
    assert inbox[0]["topics"][0]["name"] == "RDNA 5"
    assert inbox[0]["reasons"]
    story_id = inbox[0]["id"]

    marked = api.post("/api/editorial/stories/seen", json={"story_ids": [story_id], "seen": True})
    assert marked.status_code == 200
    assert api.get("/api/editorial/stories").json() == []
    assert api.get("/api/editorial/stories?state=seen").json()[0]["seen"] is True

    # Note: Using the exact same fixture client
    assert api.get("/api/editorial/stories?state=seen").json()[0]["id"] == story_id
    api.post("/api/editorial/stories/seen", json={"story_ids": [story_id], "seen": False})
    assert api.get("/api/editorial/stories").json()[0]["seen"] is False


def test_source_suggestion_review_and_one_click_add(cli_env, client):
    api = client
    source = api.post("/api/sources", json={
        "name": "Known Wire", "type": "manual", "trust_weight": .7,
    }).json()
    api.post("/api/evidence", json={
        "source_id": source["id"], "title": "RTX 50 Super details",
        "content": '<a href="https://new-origin.example/report">source report</a>',
    })
    suggestion = api.get("/api/source-suggestions").json()[0]
    suggestion_id = suggestion["id"]
    assert api.post(
        f"/api/source-suggestions/{suggestion_id}/review", json={"action": "ignore"}
    ).json()["status"] == "ignored"
    assert api.post(
        f"/api/source-suggestions/{suggestion_id}/review", json={"action": "restore"}
    ).json()["status"] == "pending"
    added = api.post(f"/api/source-suggestions/{suggestion_id}/add", json={
        "name": "New Origin", "feed_url": "https://new-origin.example/feed",
    })
    assert added.status_code == 201
    assert added.json()["url"] == "https://new-origin.example/feed"


def test_source_suggestion_add_conflict_on_duplicate_name(cli_env, client):
    """The backend correctly 409s when the inferred/given name already
    exists as a source. The frontend's addSuggestedSource() previously had
    no try/catch around this call, so this rejection became an unhandled
    promise rejection with zero visible feedback to the operator -- see the
    accompanying semantic test that the handler now surfaces the error."""
    api = client
    api.post("/api/sources", json={
        "name": "Already Registered", "type": "manual", "trust_weight": .7,
    })
    source = api.post("/api/sources", json={
        "name": "Known Wire", "type": "manual", "trust_weight": .7,
    }).json()
    api.post("/api/evidence", json={
        "source_id": source["id"], "title": "RTX 50 Super details",
        "content": '<a href="https://second-origin.example/report">source report</a>',
    })
    suggestion = api.get("/api/source-suggestions").json()[0]
    conflict = api.post(f"/api/source-suggestions/{suggestion['id']}/add", json={
        "name": "Already Registered", "feed_url": "https://second-origin.example/feed",
    })
    assert conflict.status_code == 409
    # the suggestion must remain pending -- a failed add must not silently
    # transition or lose the row
    assert api.get("/api/source-suggestions").json()[0]["id"] == suggestion["id"]


def test_discover_feed_handler_gives_feedback_when_no_feed_is_found():
    """Semantic regression test: discoverFeed() previously gave zero visible
    feedback in any case -- success, failure, or a legitimate zero-result
    search (a site with no autodiscoverable feed, or one that blocks
    automated fetches) all looked identical to 'the button did nothing'.
    The handler must now show a loading state while searching and a clear
    message when nothing is found."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function discoverFeed")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "Searching" in body
    assert "result.selected" in body
    assert "try" in body and "catch" in body


def test_add_suggested_source_handler_surfaces_errors_to_the_operator():
    """Semantic regression test for the silent-failure defect: clicking
    'Add source' on a Suggested Sources row that fails (e.g. duplicate name)
    previously produced zero visible feedback -- an unhandled promise
    rejection only visible in the browser console. The handler must now
    catch and surface the error."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function addSuggestedSource")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "try" in body and "catch" in body
    assert "err.message" in body


def _seed_x_handle_suggestion(client) -> int:
    """Directly inserts a kind=HANDLE, platform=x SourceSuggestion row, the
    same shape LegacyRadarImporter._plan_suggestions() produces for an
    imported `legacy-handle:x:...` row -- there is no API path that creates
    this shape organically in a single call, so tests build it directly via
    a side session against the same DB the client fixture is using."""
    import os
    from semi_intel.db import get_engine, get_sessionmaker
    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    session = get_sessionmaker(engine)()
    suggestion = SourceSuggestion(
        domain="legacy-handle:x:iancutress", kind=SourceSuggestionKind.HANDLE,
        platform="x", provider_key="IanCutress", inferred_name="IanCutress",
        score=0.8,
    )
    session.add(suggestion)
    session.commit()
    suggestion_id = suggestion.id
    session.close()
    return suggestion_id


def test_source_suggestions_endpoint_exposes_provider_fields(cli_env, client):
    """The Suggested Sources list previously discarded kind/platform/
    provider_key, forcing the UI to always assume every row was a website
    -- see the v0.9.3 provider-aware rewrite. The endpoint must now expose
    them so the frontend can branch on the real provider type."""
    api = client
    suggestion_id = _seed_x_handle_suggestion(api)
    rows = api.get("/api/source-suggestions?status=pending").json()
    row = next(item for item in rows if item["id"] == suggestion_id)
    assert row["kind"] == "handle"
    assert row["platform"] == "x"
    assert row["provider_key"] == "IanCutress"


def test_find_feed_rejects_a_handle_suggestion(cli_env, client):
    """Regression test for the root cause: Find Feed previously ran website
    RSS discovery against ANY suggestion, including X handles, where the
    'domain' field is a synthetic string like 'legacy-handle:x:iancutress'
    -- guaranteed to fail silently. The endpoint must now refuse outright
    for handle-kind suggestions instead of attempting a doomed fetch."""
    api = client
    suggestion_id = _seed_x_handle_suggestion(api)
    r = api.post(f"/api/source-suggestions/{suggestion_id}/discover-feed", json={})
    assert r.status_code == 400
    assert "handle" in r.json()["detail"].lower()


def test_add_suggested_source_rejects_a_handle_suggestion(cli_env, client):
    """Same guard as Find Feed: the website-only /add endpoint must not
    silently create a bogus RSS source for a handle suggestion."""
    api = client
    suggestion_id = _seed_x_handle_suggestion(api)
    r = api.post(f"/api/source-suggestions/{suggestion_id}/add", json={})
    assert r.status_code == 400
    assert "handle" in r.json()["detail"].lower()


def test_radar_review_accept_creates_an_x_source_from_a_handle_suggestion(cli_env, client):
    """The correct add workflow for a handle suggestion: the pre-existing
    /api/radar/source-suggestions/{id}/review accept action, which the
    Suggested Sources UI now calls for X-kind rows via acceptHandleSuggestion()."""
    api = client
    suggestion_id = _seed_x_handle_suggestion(api)
    r = api.post(f"/api/radar/source-suggestions/{suggestion_id}/review", json={"action": "accept"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"

    import os
    from semi_intel.db import get_engine, get_sessionmaker
    from semi_intel.domain.models import Source
    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    session = get_sessionmaker(engine)()
    added = session.get(Source, body["source_id"])
    assert added.provider == "x"
    assert added.provider_key == "IanCutress"
    session.close()

    pending = api.get("/api/source-suggestions?status=pending").json()
    assert all(item["id"] != suggestion_id for item in pending)
    added_rows = api.get("/api/source-suggestions?status=added").json()
    assert any(item["id"] == suggestion_id for item in added_rows)


def test_source_suggestion_provider_grouping_is_kind_and_platform_aware():
    """Semantic regression test for the v0.9.3 provider-aware rewrite:
    sourceSuggestionProviderGroup() must key off the suggestion's actual
    structured kind/platform fields, not infer provider from display text
    (the domain field can literally be 'legacy-handle:x:...')."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("function sourceSuggestionProviderGroup")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert 'item.kind === "domain"' in body
    assert '"website"' in body and '"x"' in body and '"unsupported"' in body


def test_x_handle_row_does_not_offer_find_feed_as_primary_action():
    """Semantic regression test for the reported defect: an X-handle
    suggestion must never render the 'Find feed' button as its primary
    action (that always failed silently, since Find Feed always assumed
    every suggestion was a website). It must render an 'Add X source'
    action instead, calling the handle-specific accept handler."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("function sourceSuggestionRow")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "Add X source" in body
    assert "acceptHandleSuggestion" in body
    # the 'Find feed' primary action must only be reachable for the website group
    find_feed_index = body.index("Find feed")
    website_group_index = body.index('group === "website"')
    assert website_group_index < find_feed_index


def test_accept_handle_suggestion_calls_radar_review_accept_endpoint():
    """Semantic regression test: acceptHandleSuggestion() must hit the
    pre-existing provider-aware accept endpoint (which reuses
    accept_source_suggestion()), not the website-only /add endpoint."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function acceptHandleSuggestion")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "/api/radar/source-suggestions/${id}/review" in body
    assert '"accept"' in body
    assert "try" in body and "catch" in body


def _seed_reddit_domain_suggestion(client, *, feed_url=None) -> int:
    """Directly inserts a kind=DOMAIN, platform=reddit SourceSuggestion --
    the shape discover_subreddit_candidates() produces -- since seeding it
    through the real generator requires collected SignalItem data this
    test doesn't need."""
    import os
    from semi_intel.db import get_engine, get_sessionmaker
    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    session = get_sessionmaker(engine)()
    suggestion = SourceSuggestion(
        domain="reddit:r/hardware", kind=SourceSuggestionKind.DOMAIN,
        platform="reddit", provider_key="hardware", inferred_name="r/hardware",
        feed_url=feed_url, score=0.6,
    )
    session.add(suggestion)
    session.commit()
    suggestion_id = suggestion.id
    session.close()
    return suggestion_id


def test_reddit_domain_suggestion_find_feed_uses_deterministic_url(cli_env, client, monkeypatch):
    """Find Feed on a Reddit suggestion must retry the exact known
    subreddit .rss URL instead of treating the synthetic 'reddit:r/...'
    domain as a real website to crawl."""
    calls = []

    def fake_validate(url):
        calls.append(url)
        return url

    monkeypatch.setattr("semi_intel.signals.source_discovery._validate_deterministic_feed", fake_validate)
    api = client
    suggestion_id = _seed_reddit_domain_suggestion(api)
    r = api.post(f"/api/source-suggestions/{suggestion_id}/discover-feed", json={})
    assert r.status_code == 200, r.text
    assert calls == ["https://www.reddit.com/r/hardware/.rss"]
    assert r.json()["selected"] == "https://www.reddit.com/r/hardware/.rss"


def test_github_domain_suggestion_add_uses_existing_rss_add_path(cli_env, client):
    """A GitHub suggestion with a validated feed_url must add through the
    exact same /add endpoint a website suggestion uses -- no separate
    GitHub-specific creation code path exists or is needed."""
    import os
    from semi_intel.db import get_engine, get_sessionmaker
    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    session = get_sessionmaker(engine)()
    suggestion = SourceSuggestion(
        domain="github:rocm/rocm", kind=SourceSuggestionKind.DOMAIN,
        platform="github", provider_key="rocm/rocm", inferred_name="rocm/rocm",
        feed_url="https://github.com/rocm/rocm/releases.atom", score=0.5,
    )
    session.add(suggestion)
    session.commit()
    suggestion_id = suggestion.id
    session.close()

    api = client
    added = api.post(f"/api/source-suggestions/{suggestion_id}/add", json={})
    assert added.status_code == 201, added.text
    assert added.json()["url"] == "https://github.com/rocm/rocm/releases.atom"


def test_radar_source_suggestions_discover_endpoint_reports_generator_status(cli_env, client):
    """The bounded discovery endpoint must return per-generator status so
    the operator can tell partial success from total failure -- not a
    bare count."""
    api = client
    r = api.post("/api/radar/source-suggestions/discover", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall_status"] in ("SUCCESS", "PARTIAL", "FAILED")
    assert "domain_and_forum" in body["generators"]
    assert "subreddit" in body["generators"]
    assert "github_repository" in body["generators"]
    assert "attribution_handle" in body["generators"]
    for generator in body["generators"].values():
        assert generator["status"] in ("SUCCESS", "PARTIAL", "FAILED")
        assert isinstance(generator["errors"], list)


def test_source_suggestion_provider_group_recognizes_reddit_github_forum():
    """Semantic regression test: sourceSuggestionProviderGroup() must
    branch on kind=domain + platform, not just kind, now that domain-kind
    rows can be website/forum/reddit/github."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("function sourceSuggestionProviderGroup")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert '"forum"' in body and '"reddit"' in body and '"github"' in body


def test_reddit_and_github_rows_share_the_add_source_workflow_not_a_new_one():
    """Semantic regression test: Reddit/GitHub suggestions must use the
    exact same 'Add source' button and discoverFeed()/addSuggestedSource()
    handlers as website suggestions -- no separate, untested code path."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("function sourceSuggestionRow")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "DOMAIN_LIKE_GROUPS.includes(group)" in body
    assert "addSuggestedSource" in body
    assert "discoverFeed" in body


def test_discover_source_suggestions_calls_the_discover_endpoint():
    """Semantic regression test for the 'Discover source suggestions'
    button handler."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function discoverSourceSuggestions")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "/api/radar/source-suggestions/discover" in body
    assert "overall_status" in body
    assert "try" in body and "catch" in body


def test_load_candidate_intelligence_calls_the_intelligence_endpoint():
    """Semantic regression test: the Candidate Intelligence panel must
    call the real consolidated endpoint and handle a failure without
    leaving the panel silently blank."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function loadCandidateIntelligence")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    assert "/api/radar/candidates/${id}/intelligence" in body
    assert "try" in body and "catch" in body
    assert "confidence" in body and "editorial_value" in body
    assert "verification_checklist" in body


def test_candidate_intelligence_panel_shows_facts_before_summary():
    """Semantic regression test for Phase 10's explicit UI requirement:
    facts (origin, confidence, editorial value, claims, checklist) must
    render before the free-text summary line, not a giant paragraph
    first."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function loadCandidateIntelligence")
    end = html.index("\n    }", start) + len("\n    }")
    body = html[start:end]
    origin_idx = body.index("Origin &amp; confirmations")
    summary_idx = body.index("og.summary")
    assert origin_idx < summary_idx


def test_discovery_settings_persist_and_disabled_provider_is_safe(cli_env, client):
    api = client
    defaults = api.get("/api/discovery/settings")
    assert defaults.status_code == 200
    assert defaults.json()["provider"] == "google_news_rss"
    assert defaults.json()["automatic"] is False
    updated = api.put("/api/discovery/settings", json={
        "enabled": False, "automatic": False, "minimum_interest_score": .65,
        "maximum_story_age_hours": 36, "cooldown_hours": 8,
        "maximum_cycles_per_story": 2, "global_cycles_per_hour": 4,
        "provider_requests_per_hour": 12, "results_per_query": 8,
    })
    assert updated.status_code == 200
    assert updated.json()["minimum_interest_score"] == .65
    assert api.get("/api/discovery/settings").json()["maximum_story_age_hours"] == 36

    source = api.post("/api/sources", json={
        "name": "Discovery Test", "type": "manual", "trust_weight": .7,
    }).json()
    api.post("/api/evidence", json={
        "source_id": source["id"], "title": "New RDNA 5 architecture details emerge",
        "content": "New RDNA5 architecture details emerge.",
    })
    story_id = api.get("/api/editorial/stories").json()[0]["id"]
    detail = api.get(f"/api/editorial/stories/{story_id}").json()
    assert detail["discovery"]["eligible"] is False
    assert "disabled" in detail["discovery"]["reason"].lower()
    run = api.post(f"/api/editorial/stories/{story_id}/discover", json={})
    assert run.status_code == 200
    assert run.json()["status"] == "skipped"
    assert run.json()["request_count"] == 0


def test_discovery_activity_and_block_domain_endpoints(cli_env, client):
    api = client
    status = api.get("/api/discovery/status")
    assert status.status_code == 200
    assert status.json()["budget"]["cycles_limit"] == 5
    blocked = api.post("/api/discovery/block-domain", json={"domain": "www.noisy-example.test"})
    assert blocked.status_code == 200
    assert blocked.json() == {"domain": "noisy-example.test", "status": "blocked"}
    rows = api.get("/api/source-suggestions?status=blocked").json()
    assert rows[0]["domain"] == "noisy-example.test"
