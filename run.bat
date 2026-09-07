@echo off
rem ============================================================
rem  OpenMinis Web - one-click launcher (backend + frontend)
rem    run.bat           -> serve built web UI  http://127.0.0.1:8765
rem    run.bat dev       -> also start Vite dev http://127.0.0.1:5173
rem  Stop with stop.bat, or close this window (Ctrl+C).
rem ============================================================
setlocal
cd /d "%~dp0"
title OpenMinis Web Server

rem Use system Python 3.10
set PYTHON_PATH=C:\Users\loo\AppData\Local\Programs\Python\Python310\python.exe

if not exist "%PYTHON_PATH%" (
    echo [ERROR] Python not found at %PYTHON_PATH%
    echo         Please install Python 3.10+ and set PYTHON_PATH
    pause
    exit /b 1
)

if "%~1"=="dev" (
    echo [run] starting backend + vite dev server ...
    "%PYTHON_PATH%" app.py --dev %2 %3 %4 %5
) else (
    echo [run] starting OpenMinis web server on http://127.0.0.1:8765 ...
    "%PYTHON_PATH%" app.py %1 %2 %3 %4 %5
)

echo.
echo [run] server exited.
pause
