@echo off
REM Builds semintel.exe AND semi-intel.exe on Windows. Run this FROM
REM Windows -- PyInstaller does not cross-compile, so running this on
REM Linux or Mac produces binaries for that OS, not .exe files.
REM
REM Usage -- works no matter where your current folder is when you run it
REM (double-clicked from inside packaging\, run as packaging\build_exe.bat
REM from the project root, run via a full path, doesn't matter):
REM     packaging\build_exe.bat
REM
REM Output:
REM   dist\semintel.exe    -- the operator CLI (install/run/status/doctor/...)
REM   dist\semi-intel.exe  -- the full detailed CLI + web dashboard
REM Neither needs Python installed to run afterward.
REM
REM NOTE: every exit point below pauses before closing. If you double-click
REM this file instead of running it from an already-open Command Prompt,
REM Windows opens a new window just for this script and closes it the
REM instant the script ends -- with no pause, success AND failure both
REM look like "the window flashed and vanished," with no way to read what
REM happened. The `pause` calls are what let you actually read the result
REM either way.

setlocal

REM Always operate from the project root (this file's parent folder),
REM regardless of what folder was current when this was launched. Without
REM this, running the script while your current folder is packaging\
REM itself (an easy mistake -- e.g. double-clicking it from inside that
REM folder in File Explorer) makes `pip install -e ".[web]"` below look for
REM pyproject.toml in packaging\ instead of the real project root, and
REM fail with "does not appear to be a Python project."
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3.10+ from python.org first.
    pause
    exit /b 1
)

echo Creating a throwaway build venv at .build_venv ...
python -m venv .build_venv
call .build_venv\Scripts\activate.bat

echo Installing the project plus web dashboard, X collection, and PyInstaller ...
pip install --upgrade pip >nul
pip install -e ".[web,x]" pyinstaller
if errorlevel 1 (
    echo pip install failed -- see output above.
    pause
    exit /b 1
)

echo Building semintel.exe (the operator CLI) ...
pyinstaller packaging\semintel.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 (
    echo PyInstaller build of semintel.exe failed -- see output above.
    pause
    exit /b 1
)

echo Building semi-intel.exe (the full CLI + web dashboard) ...
pyinstaller packaging\semi_intel.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 (
    echo PyInstaller build of semi-intel.exe failed -- see output above.
    pause
    exit /b 1
)

echo.
echo Done. Both are standalone executables in dist\ -- copy them anywhere
echo and run them without installing Python. Try:
echo     dist\semintel.exe install
echo     dist\semintel.exe --help
echo     dist\semi-intel.exe --help
echo.
pause

endlocal
