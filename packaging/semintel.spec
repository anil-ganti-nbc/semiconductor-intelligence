# PyInstaller spec for a single-file `semintel` (or `semintel.exe` on
# Windows) executable -- the operator-friendly CLI (semi_intel/operator.py).
#
# Bundles alembic.ini and migrations/, the same way semi_intel.spec does, so
# `semintel install`/`update` can run migrations in-process without a
# separate `alembic` install. Also bundles semi_intel/web/static/*.html and
# conditionally the uvicorn hidden imports, the same way semi_intel.spec
# does, so `semintel gui` works in the frozen build too -- see that spec's
# comment for why the fastapi/uvicorn collection is wrapped in try/except
# (they're an optional extra; `semintel gui` already degrades gracefully,
# printing an install hint, when they're missing).
#
# Build with: pyinstaller packaging/semintel.spec --distpath dist --workpath build
# (build_exe.bat / build_exe.sh do exactly that, plus the venv/install step,
# and also build semi_intel.spec in the same run -- you get both
# executables from one command.)
#
# IMPORTANT: PyInstaller does not cross-compile. Run this ON the OS you want
# the executable for -- see packaging/semi_intel.spec for the full note.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "alembic.ini"), "."),
    (str(ROOT / "migrations"), "migrations"),
    (str(ROOT / "semi_intel" / "web" / "static"), "semi_intel/web/static"),
]
datas += collect_data_files("tzdata")
binaries = []

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

# X is optional for source installs, but official operator builds include the
# `x` extra. Collect Playwright's lazy modules, driver and Node runtime so X
# collection is not silently amputated from the frozen executable.
try:
    import playwright  # noqa: F401

    playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
    datas += playwright_datas
    binaries += playwright_binaries
    hiddenimports += playwright_hiddenimports
except ImportError:
    pass

a = Analysis(
    [str(ROOT / "packaging" / "run_operator_cli.py")],
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
    name="semintel",
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
