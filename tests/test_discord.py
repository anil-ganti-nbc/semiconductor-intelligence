"""M6 exit criteria: embed rendering, severity gating, outbox retry."""

import pytest

from oem_radar.core.models import ChangeEvent, ChangeType, Component, Severity
from oem_radar.providers.discord import DiscordNotifier, build_embed, stars
from oem_radar.providers.sqlite import SqliteStore

from test_models import make_product


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(":memory:", str(tmp_path / "raw"))
    yield s
    s.close()


def new_product_event(sev=Severity.BREAKING):
    return ChangeEvent(product_key="s:k12", change_type=ChangeType.NEW_PRODUCT,
                       new_value="K12", severity=sev)


def test_embed_contents_unseen_cpu_warning():
    product = make_product(
        cpu=Component(raw="AMD Ryzen AI Max+ 396", canonical="ryzen-ai-max+-396", known=False)
    )
    payload = build_embed(new_product_event(), product)
    embed = payload["embeds"][0]
    assert "NEW PRODUCT" in embed["title"] and "GMKtec" in embed["title"]
    cpu_field = next(f for f in embed["fields"] if f["name"] == "CPU")
    assert "previously unseen" in cpu_field["value"]
    sev_field = next(f for f in embed["fields"] if f["name"] == "Severity")
    assert sev_field["value"] == "★★★★★"
    assert embed["url"] == product.source_url


def test_low_confidence_gets_caveat():
    payload = build_embed(new_product_event(), make_product(confidence=0.5))
    names = [f["name"] for f in payload["embeds"][0]["fields"]]
    assert "Parse confidence" in names


def test_severity_gating(store):
    n = DiscordNotifier(store, "https://hook.example", min_severity=3,
                        sender=lambda u, p: (True, None))
    n.enqueue(new_product_event(Severity.BREAKING), make_product())
    n.enqueue(ChangeEvent(product_key="s:k12", change_type=ChangeType.DESCRIPTION_CHANGED,
                          severity=Severity.MINOR), make_product())
    assert len(store.outbox_pending("discord")) == 1  # minor one suppressed
    suppressed = store.db.execute(
        "SELECT COUNT(*) c FROM notifications WHERE status='suppressed'"
    ).fetchone()["c"]
    assert suppressed == 1  # but audited, not dropped


def test_drain_success_and_retry(store):
    calls = []

    def flaky_sender(url, payload):
        calls.append(url)
        return (False, "HTTP 500") if len(calls) == 1 else (True, None)

    n = DiscordNotifier(store, "https://hook.example", 3, sender=flaky_sender)
    n.enqueue(new_product_event(), make_product())

    assert n.drain() == 0  # first attempt fails → back to pending
    row = store.db.execute("SELECT * FROM notifications").fetchone()
    assert row["status"] == "pending" and row["attempts"] == 1

    assert n.drain() == 1  # retry succeeds (ADR-1: nothing lost across runs)
    row = store.db.execute("SELECT * FROM notifications").fetchone()
    assert row["status"] == "sent" and row["sent_at"] is not None


def test_drain_without_webhook_is_safe(store):
    n = DiscordNotifier(store, None, 3)
    n.enqueue(new_product_event(), make_product())
    assert n.drain() == 0  # logged, stays pending for when webhook is configured
    assert len(store.outbox_pending("discord")) == 1


def test_stars():
    assert stars(5) == "★★★★★" and stars(2) == "★★☆☆☆"
