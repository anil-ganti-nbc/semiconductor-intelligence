from pathlib import Path

import pytest

from oem_radar.core.config import (
    ConfigError,
    load_oem_configs,
    load_radar_config,
    parse_interval,
)

REPO = Path(__file__).parent.parent


def test_parse_interval():
    assert parse_interval("90s") == 90
    assert parse_interval("30m") == 1800
    assert parse_interval("6h") == 21600
    assert parse_interval("1d") == 86400
    assert parse_interval(42) == 42
    with pytest.raises(ValueError):
        parse_interval("6 hours")


def test_shipped_config_is_valid():
    cfg = load_radar_config(REPO / "config" / "radar.yaml")
    assert cfg.store == "sqlite"
    assert cfg.severity_rules[-1].match == {}  # catch-all present
    oems = load_oem_configs(REPO / "config" / "oems")
    assert "GMKtec" in oems
    src = oems["GMKtec"].sources[0]
    assert src.engine == "shopify"
    assert src.min_interval_s == 21600


def test_broken_descriptor_reports_all_problems(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "manufacturer: {name: A}\nsources: [{id: s1, engine: shopify, base_url: x, min_interval: nonsense}]\n"
    )
    (tmp_path / "b.yaml").write_text("sources: []\n")  # missing manufacturer
    with pytest.raises(ConfigError) as ei:
        load_oem_configs(tmp_path)
    joined = " ".join(ei.value.problems)
    assert "a.yaml" in joined and "b.yaml" in joined  # both collected, not fail-fast


def test_duplicate_source_ids_rejected(tmp_path):
    body = "manufacturer: {name: %s}\nsources: [{id: dupe, engine: shopify, base_url: x}]\n"
    (tmp_path / "a.yaml").write_text(body % "A")
    (tmp_path / "b.yaml").write_text(body % "B")
    with pytest.raises(ConfigError, match="duplicate source id"):
        load_oem_configs(tmp_path)


def test_severity_out_of_range_rejected(tmp_path):
    p = tmp_path / "radar.yaml"
    p.write_text("severity_rules: [{match: {}, severity: 6}]\n")
    with pytest.raises(ConfigError):
        load_radar_config(p)
