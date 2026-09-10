@echo off
rem ============================================================
rem  OpenMinis - build the standalone Windows executable
rem
rem    build.bat            onedir  -> dist\OpenMinis\OpenMinis.exe
rem    build.bat onefile    onefile -> dist\OpenMinis.exe
rem    build.bat clean      remove build\ and dist\
rem
rem  The version comes from src\openminis\__init__.py.
rem  The web UI (web\dist) is bundled when it exists; run setup.bat
rem  first if you want the exe to serve a working page.
rem
rem  Set OPENMINIS_NOPAUSE=1 to skip the final "press any key".
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title OpenMinis Build

rem --- interpreter: prefer the project venv, fall back to system 3.10 -------
set PYTHON_PATH=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON_PATH%" set PYTHON_PATH=C:\Users\loo\AppData\Local\Programs\Python\Python310\python.exe

if not exist "%PYTHON_PATH%" (
    echo [ERROR] Python not found.
    echo         Looked for .venv\Scripts\python.exe and the default 3.10 install.
    pause
    exit /b 1
)

if /i "%~1"=="clean" (
    echo [clean] removing build\ and dist\ ...
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
    echo [clean] done.
    if not "%OPENMINIS_NOPAUSE%"=="1" pause
    exit /b 0
)

rem --- version -------------------------------------------------------------
rem Read via a helper file, not an inline -c snippet: cmd's for /f delimits
rem its command with single quotes, so sys.path.insert(0,'src') would end it.
set APP_VERSION=unknown
for /f "delims=" %%v in ('"%PYTHON_PATH%" packaging\print_version.py 2^>nul') do set APP_VERSION=%%v
echo [build] OpenMinis !APP_VERSION!

rem --- PyInstaller ---------------------------------------------------------
"%PYTHON_PATH%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [build] PyInstaller missing, installing ...
    "%PYTHON_PATH%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] could not install PyInstaller.
        if not "%OPENMINIS_NOPAUSE%"=="1" pause
        exit /b 1
    )
)

rem --- frontend ------------------------------------------------------------
if not exist "web\dist\index.html" (
    echo [WARN] web\dist is missing.
    echo        The exe will start but show the "frontend not built" page.
    echo        Run setup.bat first to build the UI into the binary.
)

rem --- shape ---------------------------------------------------------------
set OPENMINIS_ONEFILE=0
if /i "%~1"=="onefile" set OPENMINIS_ONEFILE=1

echo [build] running PyInstaller ^(this takes a minute or two^) ...
"%PYTHON_PATH%" -m PyInstaller --noconfirm --clean packaging\OpenMinis.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller failed. See the output above.
    if not "%OPENMINIS_NOPAUSE%"=="1" pause
    exit /b 1
)

echo.
if "%OPENMINIS_ONEFILE%"=="1" (
    echo [OK] dist\OpenMinis.exe  ^(v!APP_VERSION!, single file^)
) else (
    echo [OK] dist\OpenMinis\OpenMinis.exe  ^(v!APP_VERSION!^)
)
echo      Run it from a terminal if you want to watch the startup log.
if not "%OPENMINIS_NOPAUSE%"=="1" pause
exit /b 0
