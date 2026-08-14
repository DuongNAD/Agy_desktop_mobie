@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: AgyMobile - Antigravity 2.0 & Desktop Workspace Mobile Controller
:: Fast Runner for Windows (Instant Developer Launch)
:: ============================================================================

title AgyMobile (Fast Runner)

:: Resolve script directory and strip trailing backslash
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

:: Enforce UTF-8 console code page
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "VENV_PY=%SCRIPT_DIR%\.venv\Scripts\python.exe"

:: Fast Check: If .venv exists, execute immediately with zero overhead
if exist "!VENV_PY!" (
    "!VENV_PY!" -m uvicorn backend.main:app --host 0.0.0.0 --port 8765 %*
    exit /b %ERRORLEVEL%
)

:: If .venv does not exist, delegate to start.bat for full setup
echo [AgyMobile] Virtual environment (.venv) not found.
echo [AgyMobile] Redirecting to start.bat for 1-click initial setup...
echo.
call "%SCRIPT_DIR%\start.bat" %*
exit /b %ERRORLEVEL%
