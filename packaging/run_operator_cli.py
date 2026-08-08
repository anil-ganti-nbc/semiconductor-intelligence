"""PyInstaller entry point for `semintel.exe` -- the operator-friendly CLI.

Mirrors run_cli.py's role for `semi-intel.exe`: a plain script PyInstaller
can point at, that does nothing but call the real Typer app so behavior is
identical whether you run `semintel ...` (pip install) or `semintel.exe
...` (frozen build).
"""

from semi_intel.operator import app

if __name__ == "__main__":
    app()
