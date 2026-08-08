from datetime import datetime, timezone

from oem_radar.core.models import (
    ChangeEvent,
    ChangeType,
    Component,
    NormalizedProduct,
    Price,
)


def make_product(**overrides) -> NormalizedProduct:
    base = dict(
        manufacturer="GMKtec",
        model="K12",
        cpu=Component(raw="AMD Ryzen AI Max+ 395"),
        memory="96 GB",
        prices=[Price(amount=999.0, currency="USD")],
        source_url="https://www.gmktec.com/products/k12",
    )
    base.update(overrides)
    return NormalizedProduct(**base)


def test_content_hash_stable_and_deterministic():
    assert make_product().content_hash() == make_product().content_hash()


def test_observation_fields_do_not_affect_hash():
    # ADR-4: first/last seen, raw_data, confidence are observations, not product data
    a = make_product()
    b = make_product(
        first_seen=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_seen=datetime(2026, 7, 18, tzinfo=timezone.utc),
        raw_data={"vendor": "junk"},
        confidence=0.4,
    )
    assert a.content_hash() == b.content_hash()


def test_product_fields_do_affect_hash():
    assert make_product().content_hash() != make_product(memory="128 GB").content_hash()
    assert (
        make_product().content_hash()
        != make_product(cpu=Component(raw="AMD Ryzen AI Max+ 396")).content_hash()
    )


def test_canonical_json_key_order_independent():
    # formatting/ordering noise structurally cannot create diffs
    p = make_product()
    assert p.canonical_json() == NormalizedProduct(**p.model_dump()).canonical_json()


def test_event_dedup_key_stable():
    e1 = ChangeEvent(product_key="s:k12", change_type=ChangeType.PRICE_CHANGED,
                     field="prices", old_value=999, new_value=899)
    e2 = ChangeEvent(product_key="s:k12", change_type=ChangeType.PRICE_CHANGED,
                     field="prices", old_value=999, new_value=899)
    assert e1.dedup_key() == e2.dedup_key()
    e3 = ChangeEvent(product_key="s:k12", change_type=ChangeType.PRICE_CHANGED,
                     field="prices", old_value=999, new_value=799)
    assert e1.dedup_key() != e3.dedup_key()
