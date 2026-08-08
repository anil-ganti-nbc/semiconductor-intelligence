@echo off
setlocal
rem OEM Radar launcher (Windows). Self-contained: no pip install -e needed.
rem Webhook: prefer env var, else the file-based one. The CLI already reads
rem config\discord_webhook.txt, so nothing to set here.

cd /d "%~dp0"
set PYTHONPATH=src

rem Find a REAL Python. Try the py launcher first (it dodges the Microsoft
rem Store 'python.exe' stub that hijacks the plain 'python' command), then
rem python, then python3. Each candidate is version-checked, not just found.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY ( python3 --version >nul 2>nul && set "PY=python3" )
if not defined PY (
    echo Could not find a working Python.
    echo Test it yourself in this window:   py --version    (or)   python --version
    echo If neither prints a version, install from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer.
    echo If 'python' opens the Microsoft Store, disable the stub:
    echo   Settings ^> Apps ^> Advanced app settings ^> App execution aliases ^> turn OFF python.exe
    pause & exit /b 1
)

rem First run: install the three runtime dependencies if missing
%PY% -c "import pydantic, yaml, requests" >nul 2>nul || (
    echo First run: installing dependencies pydantic, PyYAML, requests...
    %PY% -m pip install --quiet pydantic PyYAML requests
)

if "%~1"=="" (
    %PY% -m oem_radar.cli run
) else (
    %PY% -m oem_radar.cli %*
)

rem Keep the window open on double-click (no args); scheduled/CLI runs exit clean
if "%~1"=="" (echo %cmdcmdline% | find /i "%~f0" >nul && pause)
