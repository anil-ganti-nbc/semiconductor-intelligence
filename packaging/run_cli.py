"""PyInstaller entry point.

PyInstaller needs a plain script to point at, not a console_script name from
pyproject.toml -- this file is that script. It does nothing but call the
same Typer app the `semi-intel` command normally calls, so behavior is
identical whether you run `semi-intel ...` (pip install) or `semi-intel.exe
...` (frozen build).
"""

from semi_intel.cli import app

if __name__ == "__main__":
    app()
