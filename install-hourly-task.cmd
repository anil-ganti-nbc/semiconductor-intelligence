@echo off
setlocal
rem One-time setup: registers a Windows Scheduled Task that runs the crawler
rem silently every hour. Run this ONCE (double-click is fine).
cd /d "%~dp0"
set TASK=OEM Radar Hourly Crawl
set ACTION=wscript.exe "%~dp0crawl-silent.vbs"

schtasks /query /tn "%TASK%" >nul 2>nul && (
    echo Task already exists. Recreating with the current path...
    schtasks /delete /tn "%TASK%" /f >nul 2>nul
)

rem /sc hourly runs every hour; /ru "%USERNAME%" runs as you; the task runs
rem whether or not you're logged in only if you tick that box, but hourly-while-
rem logged-in is fine for a desktop and needs no stored password.
schtasks /create /tn "%TASK%" /tr "%ACTION%" /sc hourly /mo 1 /f
if %errorlevel%==0 (
    echo.
    echo Installed. The crawler now runs silently every hour.
    echo   - See results anytime:   dashboard.cmd  (or: oem-radar dashboard)
    echo   - Watch the run log:     data\crawl-runs.log
    echo   - Run one now to test:   schtasks /run /tn "%TASK%"
    echo   - Remove it later:       uninstall-hourly-task.cmd
) else (
    echo.
    echo Install failed. Try running this file as Administrator
    echo   ^(right-click ^> Run as administrator^), or see the README.
)
echo.
pause
