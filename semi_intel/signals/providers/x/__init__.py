"""Optional X (Twitter) signal provider.

Ported from Signal Radar's `providers/x/` (see PHASE0_AUDIT.md section 5) with
its safety properties kept intact, unchanged (brief section 17):

- Importing this package NEVER requires Playwright -- it is imported lazily,
  only when a collection is actually attempted, so the base application (RSS,
  everything else) works with the `x` extra completely absent.
- The app never logs in itself. `auth.py` only replays cookies obtained from
  a browser where a human already logged in normally -- the single most
  bot-detectable action (automated login) is never performed.
- No fingerprint spoofing or anti-detection machinery is implemented, by
  deliberate choice carried over from Signal Radar's own documented decision
  (see its HANDOFF.md "Decisions already challenged").
- The session file lives wherever the operator points
  `SEMI_INTEL_X_SESSION_PATH` (default: `./x_session.json`, same
  cwd-relative convention as `semi_intel.db` itself) -- never inside the
  package, never packaged into the distributable, never logged.
"""

from semi_intel.signals.providers.x.provider import XProvider

__all__ = ["XProvider"]
