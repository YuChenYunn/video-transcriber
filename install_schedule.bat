@echo off
setlocal

rem ====== Daily transcript mailer - Scheduled Task installer ======
rem Double-click to run. Registers a daily task that runs run_daily_mail.bat at 21:00.

set "TASK_NAME=VideoTranscriberDailyMail"
set "SCRIPT_DIR=%~dp0"
set "RUN_BAT=%SCRIPT_DIR%run_daily_mail.bat"

:menu
cls
echo ============================================
echo   Daily Transcript Mailer - Task Manager
echo ============================================
echo.
echo   1. Install  (run every day at 21:00)
echo   2. Run now  (test immediately)
echo   3. Show task status
echo   4. Uninstall
echo   5. Exit
echo.
set /p "choice=Enter 1-5: "

if "%choice%"=="1" goto install
if "%choice%"=="2" goto runnow
if "%choice%"=="3" goto query
if "%choice%"=="4" goto uninstall
if "%choice%"=="5" exit /b 0
echo Invalid choice.
pause
goto menu

:install
echo.
echo Registering scheduled task...
schtasks /Create /SC DAILY /TN "%TASK_NAME%" /TR "\"%RUN_BAT%\"" /ST 21:00 /F
if errorlevel 1 (
    echo.
    echo *** FAILED. If it says access denied, right-click this .bat and "Run as administrator".
) else (
    echo.
    echo *** OK. Task name: %TASK_NAME%
    echo *** run_daily_mail.bat will run every day at 21:00.
    echo.
    echo NOTE: The PC must be ON and awake at 21:00 for the task to fire.
    echo If it was off at that time, Windows runs it once on next boot
    echo (you can change this in "Task Scheduler").
)
echo.
pause
goto menu

:runnow
echo.
schtasks /Run /TN "%TASK_NAME%"
if errorlevel 1 (
    echo *** FAILED. Make sure the task is installed first (option 1).
) else (
    echo *** Triggered. Check mailer.log and your inbox shortly.
)
echo.
pause
goto menu

:query
echo.
schtasks /Query /TN "%TASK_NAME%" /V /FO LIST
echo.
pause
goto menu

:uninstall
echo.
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
    echo *** FAILED (task may not exist).
) else (
    echo *** Deleted task: %TASK_NAME%
)
echo.
pause
goto menu
