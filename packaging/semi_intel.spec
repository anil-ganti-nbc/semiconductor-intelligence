# PyInstaller spec for a single-file `semi-intel` (or `semi-intel.exe` on
# Windows) executable, bundling:
#   - the whole semi_intel package (CLI + web dashboard + everything else)
#   - alembic.ini and migrations/ (so `semi-intel db upgrade` works without
#     a separate `alembic` install -- see semi_intel/cli.py's _alembic_config())
#   - semi_intel/web/static/*.html (so `semi-intel web serve` works without
#     the source tree on disk -- see semi_intel/web/app.py's _static_dir())
#
# Build with: pyinstaller packaging/semi_intel.spec --distpath dist --workpath build
# (build_exe.bat / build_exe.sh do exactly that, plus the venv/install step.)
#
# IMPORTANT: PyInstaller does not cross-compile. Run this ON the OS you want
# the executable for -- run it on Windows to get semi-intel.exe, on Linux/Mac
# to get a native binary for those. There is no way to produce a Windows .exe
# from a Linux or Mac machine (or vice versa) with PyInstaller alone.

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# Repo root = the parent of this spec file's directory (packaging/).
ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "alembic.ini"), "."),
    (str(ROOT / "migrations"), "migrations"),
    (str(ROOT / "semi_intel" / "web" / "static"), "semi_intel/web/static"),
]
datas += collect_data_files("tzdata")
binaries = []

# fastapi/uvicorn are an optional extra (the `web` group) -- if they aren't
# installed in the environment running PyInstaller, skip trying to collect
# them rather than failing the whole build. `semi-intel web serve` already
# degrades gracefully (prints an install hint) when they're missing, both
# from source and frozen.
hiddenimports = [
    "alembic",
    "alembic.runtime.migration",
    "sqlalchemy.sql.default_comparator",
    # Starlette dispatches synchronous endpoints through AnyIO, whose backend
    # is imported dynamically and therefore needs an explicit frozen import.
    "anyio._backends",
    "anyio._backends._asyncio",
    "anyio.abc",
    "sniffio",
]
try:
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401

    hiddenimports += [
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ]
except ImportError:
    pass

# X is optional for source installs, but when the build environment includes
# the `x` extra the frozen executable must carry Playwright's Python modules,
# driver and Node runtime. PyInstaller cannot reliably discover all of these
# through the provider's lazy imports.
try:
    import playwright  # noqa: F401

    playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
    datas += playwright_datas
    binaries += playwright_binaries
    hiddenimports += playwright_hiddenimports
except ImportError:
    pass

a = Analysis(
    [str(ROOT / "packaging" / "run_cli.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="semi-intel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
