"""Runtime path resolution for OEM Radar."""

import os
import sys
import warnings
from pathlib import Path


def _get_env(key: str) -> str | None:
    val = os.environ.get(key)
    return val if val and val.strip() else None

def get_runtime_root() -> Path:
    """Return the canonical user-owned runtime root for OEM Radar."""
    if env := _get_env("OEM_RADAR_HOME"):
        return Path(env)
        
    if os.name == "nt":
        base = _get_env("LOCALAPPDATA") or _get_env("APPDATA") or str(Path.home())
        return Path(base) / "OEMRadar"
    elif os.name == "posix" and sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OEMRadar"
    else:
        xdg = _get_env("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "OEMRadar"
        return Path.home() / ".local" / "share" / "OEMRadar"


def _resolve(env_override: str, canonical_sub: str, legacy_rel: str) -> Path:
    if env := _get_env(env_override):
        return Path(env)
    
    canonical = get_runtime_root() / canonical_sub
    if canonical.exists():
        return canonical
        
    allow_legacy = _get_env("OEM_RADAR_ALLOW_LEGACY_PATHS")
    if allow_legacy is None or allow_legacy.strip().lower() in ("1", "true", "yes"):
        legacy = Path.cwd() / legacy_rel
        if legacy.exists():
            warnings.warn(
                f"Legacy path {legacy_rel} found in working directory. "
                f"Future versions will ignore this. Suggested migration to: {canonical}",
                category=DeprecationWarning,
                stacklevel=2,
            )
            return legacy
        
    return canonical


def get_discord_webhook_path(config_dir: Path) -> Path:
    allow_legacy = _get_env("OEM_RADAR_ALLOW_LEGACY_PATHS")
    if allow_legacy is None or allow_legacy.strip().lower() in ("1", "true", "yes"):
        legacy = config_dir / "discord_webhook.txt"
        if legacy.exists():
            warnings.warn(
                f"Legacy webhook {legacy} found. "
                "Please migrate to canonical config.",
                category=DeprecationWarning,
                stacklevel=2,
            )
            return legacy
    return get_runtime_root() / "config" / "discord_webhook.txt"


def get_db_path(default_db_path: str) -> str:
    if default_db_path != "data/radar.db":
        return default_db_path
    return str(_resolve("OEM_RADAR_DB", "data/radar.db", "data/radar.db"))


def get_raw_dir(default_raw_dir: str) -> str:
    if default_raw_dir != "data/raw":
        return default_raw_dir
    return str(_resolve("OEM_RADAR_RAW_DIR", "raw", "data/raw"))


def get_lock_path(default_lock: str) -> str:
    if default_lock != "data/oem-radar.lock":
        return default_lock
    return str(_resolve("OEM_RADAR_LOCK", "oem-radar.lock", "data/oem-radar.lock"))
