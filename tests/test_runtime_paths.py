"""Test suite for runtime path precedence and safety."""
import os
import sys
import pytest
from pathlib import Path, PureWindowsPath

from semi_intel.paths import get_runtime_root, _resolve, get_db_path, get_x_session_path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove SEMINTEL related env vars."""
    for key in list(os.environ.keys()):
        if key.startswith("SEMINTEL_") or key.startswith("OEM_RADAR_") or key in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
            monkeypatch.delenv(key, raising=False)


def test_empty_env_vars_ignored(clean_env, monkeypatch):
    monkeypatch.setenv("SEMINTEL_HOME", "   ")
    root = get_runtime_root()
    assert root != Path("   ")
    
    monkeypatch.setenv("SEMINTEL_DB", "")
    db = get_db_path()
    assert str(db) != "."
    assert db.name == "semi_intel.db"


def test_windows_localappdata(clean_env, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    # Python 3.13's pathlib refuses to instantiate a concrete WindowsPath on a
    # non-Windows host even when os.name is monkeypatched (UnsupportedOperation).
    # PureWindowsPath does the same string/segment algebra without that concrete-
    # class OS guard, so it exercises get_runtime_root()'s Windows branch and its
    # exact backslash formatting on any host OS, matching this test's original intent.
    monkeypatch.setattr("semi_intel.paths.Path", PureWindowsPath)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")
    root = get_runtime_root()
    assert str(root) == r"C:\Users\Test\AppData\Local\SemiIntel"


@pytest.mark.skipif(os.name == 'nt', reason="Pathlib rejects posix test on Windows")
def test_linux_xdg(clean_env, monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    # os.name alone can't distinguish Linux from macOS (both report "posix");
    # get_runtime_root() also checks sys.platform == "darwin", so that must be
    # patched too or this test silently exercises the macOS branch instead of
    # the intended Linux/XDG one when actually run on a Mac.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/test/.local/share")
    root = get_runtime_root()
    assert str(root) == "/home/test/.local/share/SemiIntel"


def test_legacy_fallback_when_enabled(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr("semi_intel.paths.get_runtime_root", lambda: tmp_path / "canonical")
    (tmp_path / "legacy_cwd").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path / "legacy_cwd")
    
    legacy_file = tmp_path / "legacy_cwd" / "x_session.json"
    legacy_file.touch()

    # Default is enabled
    with pytest.warns(DeprecationWarning, match="Legacy path x_session.json found"):
        p = get_x_session_path()
        assert p == legacy_file

    # Disabled
    monkeypatch.setenv("SEMINTEL_ALLOW_LEGACY_PATHS", "0")
    p = get_x_session_path()
    assert p == tmp_path / "canonical" / "browser" / "x_session.json"


def test_canonical_wins_over_legacy(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr("semi_intel.paths.get_runtime_root", lambda: tmp_path / "canonical")
    (tmp_path / "legacy_cwd").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path / "legacy_cwd")
    
    legacy_file = tmp_path / "legacy_cwd" / "x_session.json"
    legacy_file.touch()
    
    canonical_dir = tmp_path / "canonical" / "browser"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_file = canonical_dir / "x_session.json"
    canonical_file.touch()
    
    # Canonical exists and wins
    p = get_x_session_path()
    assert p == canonical_file


def test_env_wins_over_canonical(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr("semi_intel.paths.get_runtime_root", lambda: tmp_path / "canonical")
    canonical_dir = tmp_path / "canonical" / "data"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_file = canonical_dir / "semi_intel.db"
    canonical_file.touch()
    
    monkeypatch.setenv("SEMINTEL_DB", str(tmp_path / "env_db.sqlite"))
    
    p = get_db_path()
    assert p == tmp_path / "env_db.sqlite"


def test_redaction_in_warnings(clean_env, monkeypatch, tmp_path):
    """Verify secrets are not accidentally printed in warnings."""
    monkeypatch.setattr("semi_intel.paths.get_runtime_root", lambda: tmp_path / "canonical")
    (tmp_path / "legacy_cwd").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path / "legacy_cwd")
    
    (tmp_path / "legacy_cwd" / "config").mkdir(parents=True, exist_ok=True)
    legacy_file = tmp_path / "legacy_cwd" / "config" / "discord_webhook.txt"
    legacy_file.write_text("https://discord.com/api/webhooks/super_secret_token")
    
    from semi_intel.paths import get_discord_webhook_path
    
    with pytest.warns(DeprecationWarning) as record:
        get_discord_webhook_path()
        
    for warn in record.list:
        assert "super_secret_token" not in str(warn.message)
