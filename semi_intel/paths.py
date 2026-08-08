"""Runtime path resolution and backwards compatibility for Semi Intel."""

import os
import sys
import warnings
from pathlib import Path


def _get_env(key: str) -> str | None:
    val = os.environ.get(key)
    return val if val and val.strip() else None

def get_runtime_root() -> Path:
    """Return the canonical user-owned runtime root, checking environment."""
    if env := _get_env("SEMINTEL_HOME"):
        return Path(env)
    
    if os.name == "nt":
        base = _get_env("LOCALAPPDATA") or _get_env("APPDATA") or str(Path.home())
        return Path(base) / "SemiIntel"
    elif os.name == "posix" and sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SemiIntel"
    else:
        # Check XDG_DATA_HOME for Linux natively
        xdg = _get_env("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "SemiIntel"
        return Path.home() / ".local" / "share" / "SemiIntel"

def _resolve(env_override: str, canonical_sub: str, legacy_rel: str) -> Path:
    if env := _get_env(env_override):
        return Path(env)
    
    canonical = get_runtime_root() / canonical_sub
    if canonical.exists():
        return canonical
        
    allow_legacy = _get_env("SEMINTEL_ALLOW_LEGACY_PATHS")
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
        
    # If neither exists, default to canonical
    return canonical

def get_db_path() -> Path:
    return _resolve("SEMINTEL_DB", "data/semi_intel.db", "semi_intel.db")

def get_x_session_path() -> Path:
    return _resolve("SEMINTEL_X_SESSION", "browser/x_session.json", "x_session.json")

def get_x_profile_dir() -> Path:
    return _resolve("SEMINTEL_X_PROFILE", "browser/.x_browser_profile", ".x_browser_profile")

def get_discord_webhook_path() -> Path:
    return _resolve("SEMINTEL_DISCORD_WEBHOOK_FILE", "config/discord_webhook.txt", "config/discord_webhook.txt")

def get_diagnostics_dir() -> Path:
    return _resolve("SEMINTEL_DIAGNOSTICS_DIR", "diagnostics", "diagnostics")

def ensure_runtime_dirs() -> None:
    """Create the runtime root and expected subdirectories."""
    dirs = [
        get_runtime_root() / "config",
        get_runtime_root() / "secrets",
        get_runtime_root() / "browser",
        get_runtime_root() / "data",
        get_runtime_root() / "diagnostics",
        get_runtime_root() / "logs",
        get_runtime_root() / "exports",
        get_runtime_root() / "backups",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
