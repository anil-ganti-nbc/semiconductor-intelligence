"""Frozen dashboard dependencies that PyInstaller cannot discover statically."""

import json
from pathlib import Path


def test_both_executables_bundle_anyio_asyncio_backend():
    for name in ("semintel.spec", "semi_intel.spec"):
        spec = (Path("packaging") / name).read_text(encoding="utf-8")
        assert '"anyio._backends._asyncio"' in spec
        assert '"sniffio"' in spec


def test_official_frozen_builds_include_optional_x_runtime():
    for name in ("semintel.spec", "semi_intel.spec"):
        spec = (Path("packaging") / name).read_text(encoding="utf-8")
        assert 'collect_all("playwright")' in spec
        assert "binaries=playwright_binaries" not in spec
        assert "binaries=binaries" in spec

    for name in ("build_exe.bat", "build_exe.sh"):
        script = (Path("packaging") / name).read_text(encoding="utf-8")
        assert ".[web,x]" in script


def test_private_operator_checkpoint_config_stays_portable():
    config = json.loads(Path("semintel.config.json").read_text(encoding="utf-8"))
    assert config == {"data_dir": ".", "db_url": "sqlite:///semi_intel.db"}
