@echo off
rem ============================================================
rem  OpenMinis TUI - terminal UI launcher (Textual)
rem  Equals the console script:  minis-tui
rem ============================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\minis-tui.exe" (
    ".venv\Scripts\minis-tui.exe" %*
) else (
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" -m openminis.tui.app %*
    ) else (
        echo [ERROR] Missing .venv - setup first:   cd python ^&^& uv sync
        pause
        exit /b 1
    )
)
