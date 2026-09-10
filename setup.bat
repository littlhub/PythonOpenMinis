@echo off
rem ============================================================
rem  OpenMinis - first-time setup
rem
rem  The web UI (web\dist) is a build artifact and is NOT part of
rem  the repository, so a fresh download cannot serve a page until
rem  it is built. Run this once, then use run.bat.
rem
rem  Needs Node.js 18+ in PATH. Python deps are handled by run.bat.
rem ============================================================
setlocal
cd /d "%~dp0"
title OpenMinis Setup

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js not found in PATH.
    echo         Install Node 18+ from https://nodejs.org/ and retry.
    pause
    exit /b 1
)

echo [1/2] Installing frontend dependencies ...
pushd web
call npm install
if errorlevel 1 (
    echo [ERROR] npm install failed.
    popd
    pause
    exit /b 1
)

echo.
echo [2/2] Building frontend into web\dist ...
call npm run build
if errorlevel 1 (
    echo [ERROR] npm run build failed.
    popd
    pause
    exit /b 1
)
popd

echo.
echo [OK] Setup complete. Start the app with run.bat
pause
