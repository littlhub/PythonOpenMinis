@echo off
rem ============================================================
rem  OpenMinis Web - stop the service started by run.bat / app.py
rem  Primary: pid file written by app.py (.minis-server.pid)
rem  Fallback: any process listening on the default port 8765
rem  (only used if the pid file is missing/unreadable).
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title OpenMinis Web - Stop

set "PIDFILE=.minis-server.pid"
set "PID="
set "STOPPED="

rem --- 1) primary: read pid file (note: use !PID! inside blocks ---
rem     because %PID% would expand at parse time, before set /p runs)
if exist "%PIDFILE%" set /p PID=<"%PIDFILE%"
if exist "%PIDFILE%" del "%PIDFILE%" >nul 2>&1

if defined PID (
    taskkill /PID !PID! /T /F >nul 2>&1
    if not errorlevel 1 (
        echo [OK] stopped pid !PID!
        set "STOPPED=1"
    ) else (
        echo [INFO] pid !PID! is not running anymore
    )
) else (
    echo [INFO] pid file empty or unreadable - using port fallback
)

rem --- 2) fallback: kill the process listening on default port 8765 ---
if not defined STOPPED (
    echo [INFO] scanning for a process on port 8765 ...
    powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if ($c) { $c | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Host ('[OK] stopped pid ' + $_) }; exit 0 } else { Write-Host '[INFO] nothing listening on port 8765'; exit 0 }"
)

echo.
echo [stop] done.
pause
