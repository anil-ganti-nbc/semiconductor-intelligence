#!/usr/bin/env bash
# Builds standalone semintel AND semi-intel binaries on Linux or Mac. Run
# this on the OS you want the binaries for -- PyInstaller does not
# cross-compile, so this script cannot produce Windows .exe files; use
# build_exe.bat on Windows for that.
#
# Usage -- works no matter what your current directory is when you run it:
#     bash packaging/build_exe.sh
#     cd packaging && bash build_exe.sh
#     bash /full/path/to/packaging/build_exe.sh
#
# Output:
#   dist/semintel    -- the operator CLI (install/run/status/doctor/...)
#   dist/semi-intel  -- the full detailed CLI + web dashboard
# Neither needs Python installed to run afterward.
set -euo pipefail

# Always operate from the project root (this file's parent folder),
# regardless of the caller's current directory -- otherwise running this
# from inside packaging/ itself makes `pip install -e ".[web]"` below look
# for pyproject.toml in packaging/ instead of the real project root, and
# fail with "does not appear to be a Python project."
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found on PATH. Install Python 3.10+ first." >&2
    exit 1
fi

echo "Creating a throwaway build venv at .build_venv ..."
python3 -m venv .build_venv
source .build_venv/bin/activate

echo "Installing the project plus web dashboard, X collection, and PyInstaller ..."
pip install --upgrade pip >/dev/null
pip install -e ".[web,x]" pyinstaller

echo "Building semintel (the operator CLI) ..."
pyinstaller packaging/semintel.spec --distpath dist --workpath build --noconfirm

echo "Building semi-intel (the full CLI + web dashboard) ..."
pyinstaller packaging/semi_intel.spec --distpath dist --workpath build --noconfirm

echo
echo "Done. Both are standalone executables in dist/ -- copy them anywhere"
echo "and run them without installing Python. Try:"
echo "    dist/semintel install"
echo "    dist/semintel --help"
echo "    dist/semi-intel --help"
