@echo off
rem Daily transcript mailer - entry point for scheduled task
rem Called by Windows Task Scheduler at 21:00; output appended to mailer.log
cd /d "%~dp0"
python daily_mailer.py >> mailer.log 2>&1
