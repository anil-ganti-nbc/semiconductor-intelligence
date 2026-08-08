@echo off
setlocal
rem OEM Radar dashboard launcher (Windows). Opens the local web UI in your browser.
cd /d "%~dp0"
set PYTHONPATH=src

rem Try the py launcher first (dodges the Microsoft Store python.exe stub).
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY ( python3 --version >nul 2>nul && set "PY=python3" )
if not defined PY (
    echo Could not find a working Python. Try:  py --version
    echo Install from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
    echo or if 'python' opens the Microsoft Store: Settings ^> Apps ^> Advanced app settings
    echo ^> App execution aliases ^> turn OFF python.exe
    pause & exit /b 1
)
%PY% -c "import pydantic, yaml, requests" >nul 2>nul || (
    echo Installing dependencies...
    %PY% -m pip install --quiet pydantic PyYAML requests
)

%PY% -m oem_radar.cli dashboard %*
