import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parents[1]
METADATA = Path(SPECPATH) / "build" / "metadata"
METADATA.mkdir(parents=True, exist_ok=True)
REVISION = METADATA / "revision.txt"
REVISION.write_text(os.environ.get("SEMINTEL_BUILD_REVISION", "local-development") + "\n", encoding="utf-8")

datas = [
    (str(ROOT / "alembic.ini"), "."),
    (str(ROOT / "migrations"), "migrations"),
    (str(ROOT / "semi_intel" / "web" / "static"), "semi_intel/web/static"),
    (str(REVISION), "metadata"),
] + collect_data_files("tzdata")

a = Analysis(
    [str(ROOT / "native" / "macos" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[
        "alembic", "alembic.runtime.migration", "sqlalchemy.sql.default_comparator",
        "anyio._backends", "anyio._backends._asyncio", "anyio.abc", "sniffio",
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    ],
    excludes=["playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name="Semiconductor Intelligence", console=False, exclude_binaries=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Semiconductor Intelligence")
app = BUNDLE(
    coll,
    name="Semiconductor Intelligence.app",
    bundle_identifier="com.clank.semiconductorintelligence.fieldtest",
)
