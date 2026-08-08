@echo off
set TASK=OEM Radar Hourly Crawl
schtasks /delete /tn "%TASK%" /f
echo Removed the hourly crawl task (if it existed). The crawler and dashboard
echo still work manually via start-radar.cmd and dashboard.cmd.
pause
