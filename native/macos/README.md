# Semiconductor Intelligence macOS field test

This Finder app runs the canonical editorial dashboard in read-only field-test
mode. Mutable state is isolated beneath `~/Library/Application Support/SemiIntel/`.
It binds only to loopback, waits for readiness before opening the browser, does
not run collectors, and blocks dashboard mutations, scheduling, and external
delivery. Playwright, X sessions, webhook secrets, databases, and other operator
state are never bundled.

Build from the repository root:

```bash
PYTHON="$(pwd)/.venv/bin/python" native/macos/build.sh
```
