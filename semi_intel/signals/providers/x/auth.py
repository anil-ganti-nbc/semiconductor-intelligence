"""Session import -- reuse a human-authenticated X session instead of logging in.

Ported unchanged from Signal Radar (see semi_intel/signals/providers/x/__init__.py
for the safety rationale). X keeps a logged-in session in two cookies:

  * auth_token  -- the session token (httpOnly)
  * ct0         -- the CSRF token

You obtain them once from a browser where you're already logged in normally,
and this module turns them into a Playwright storage_state the provider
injects. The app therefore never performs an automated login.

Accepts several input shapes so you can use whatever is convenient:
  * the two raw values (auth_token, ct0)
  * a Cookie-Editor / EditThisCookie JSON export (array of {name,value,...})
  * a Netscape cookies.txt export
  * a Playwright storage_state.json (passed through)

Nothing in this module ever prints or logs a cookie value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DOMAINS = (".x.com", ".twitter.com")
_FAR_FUTURE = 4102444800  # 2100-01-01


def build_cookies(auth_token: str, ct0: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for domain in _DOMAINS:
        cookies.append({"name": "auth_token", "value": auth_token, "domain": domain,
                        "path": "/", "httpOnly": True, "secure": True,
                        "sameSite": "None", "expires": _FAR_FUTURE})
        cookies.append({"name": "ct0", "value": ct0, "domain": domain, "path": "/",
                        "httpOnly": False, "secure": True, "sameSite": "Lax",
                        "expires": _FAR_FUTURE})
    return cookies


def _from_editor_export(items: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        name = it.get("name")
        if name in ("auth_token", "ct0") and it.get("value"):
            out[name] = it["value"]
    return out


def _from_netscape(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5] in ("auth_token", "ct0"):
            out[parts[5]] = parts[6]
    return out


def parse_input(raw: str) -> dict[str, str]:
    """Extract {auth_token, ct0} from any supported export text."""
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return _from_editor_export(data)
        if isinstance(data, dict):
            if "cookies" in data and isinstance(data["cookies"], list):
                return _from_editor_export(data["cookies"])
            return {k: v for k, v in data.items() if k in ("auth_token", "ct0")}
    except json.JSONDecodeError:
        pass
    if "\t" in raw:
        got = _from_netscape(raw)
        if got:
            return got
    out: dict[str, str] = {}
    for chunk in raw.replace("\n", ";").split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            k = k.strip()
            if k in ("auth_token", "ct0"):
                out[k] = v.strip()
    return out


def storage_state_from_cookies(auth_token: str, ct0: str) -> dict[str, Any]:
    return {"cookies": build_cookies(auth_token, ct0), "origins": []}


def write_session(path: str | Path, auth_token: str, ct0: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(storage_state_from_cookies(auth_token, ct0)), encoding="utf-8")
    return p


def load_session(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("cookies", [])
