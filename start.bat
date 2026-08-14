@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: AgyMobile - Antigravity 2.0 & Desktop Workspace Mobile Controller
:: 1-Click Startup & Setup Launcher for Windows
:: ============================================================================

title AgyMobile - Desktop and Mobile Controller

:: Resolve script directory and strip trailing backslash
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

:: Enforce UTF-8 console code page for ASCII QR code rendering
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "HOST=0.0.0.0"
set "PORT=8765"
set "OPEN_BROWSER=1"
set "FORCE_INSTALL=0"
set "UVICORN_ARGS="

:: Parse arguments and filter launcher-specific flags
for %%a in (%*) do (
    if /i "%%a"=="--no-browser" (
        set "OPEN_BROWSER=0"
    ) else if /i "%%a"=="--install" (
        set "FORCE_INSTALL=1"
    ) else if /i "%%a"=="--sync" (
        set "FORCE_INSTALL=1"
    ) else (
        set "UVICORN_ARGS=!UVICORN_ARGS! %%a"
    )
)

echo.
echo ==============================================================================
echo   ANTIGRAVITY 2.0 ^& DESKTOP WORKSPACE MOBILE CONTROLLER (AgyMobile)
echo   1-Click Startup Launcher for Windows
echo ==============================================================================
echo.

:: ----------------------------------------------------------------------------
:: 1. Toolchain & Python Interpreter Detection
:: ----------------------------------------------------------------------------
set "UV_EXE="
set "BASE_PY="
set "VENV_DIR=%SCRIPT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

:: Check for Astral uv
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    for /f "tokens=*" %%i in ('where uv') do (
        if not defined UV_EXE set "UV_EXE=%%i"
    )
)
if not defined UV_EXE (
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
    if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
    if exist "%LocalAppData%\Programs\uv\uv.exe" set "UV_EXE=%LocalAppData%\Programs\uv\uv.exe"
)

if defined UV_EXE (
    echo [AgyMobile] Found uv package manager: !UV_EXE!
)

:: Check for existing valid .venv
if exist "!VENV_PY!" (
    "!VENV_PY!" -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        set "BASE_PY=!VENV_PY!"
        echo [AgyMobile] Found existing virtual environment: .venv
        goto :check_dependencies
    )
)

:: Search for system Python 3.10+
:: Check py launcher
py -3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "BASE_PY=py -3"
    goto :create_venv
)

:: Check python command in PATH
python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "BASE_PY=python"
    goto :create_venv
)

:: Check standard Windows Python installation directories
for %%V in (313 312 311 310) do (
    if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
        "%LocalAppData%\Programs\Python\Python%%V\python.exe" -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "BASE_PY=%LocalAppData%\Programs\Python\Python%%V\python.exe"
            goto :create_venv
        )
    )
    if exist "%ProgramFiles%\Python%%V\python.exe" (
        "%ProgramFiles%\Python%%V\python.exe" -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "BASE_PY=%ProgramFiles%\Python%%V\python.exe"
            goto :create_venv
        )
    )
)

:: If uv is installed, bootstrap python
if defined UV_EXE (
    echo [AgyMobile] Using uv to bootstrap Python environment...
    "!UV_EXE!" venv "%VENV_DIR%" --python 3.11 >nul 2>&1
    if exist "!VENV_PY!" (
        set "BASE_PY=!VENV_PY!"
        goto :check_dependencies
    )
)

:: Python not found error
echo.
echo [ERROR] Python 3.10+ is required but was not detected on this system.
echo.
echo Please install Python 3.11+ using one of the following methods:
echo   1. Windows Package Manager:  winget install Python.Python.3.11
echo   2. Official Python Website:  https://www.python.org/downloads/
echo   3. Astral uv Package Tool:   winget install astral-sh.uv
echo.
echo * Note: When installing from python.org, check "Add Python to PATH".
echo.
pause
exit /b 1

:: ----------------------------------------------------------------------------
:: 2. Virtual Environment Creation
:: ----------------------------------------------------------------------------
:create_venv
if not exist "!VENV_PY!" (
    echo [AgyMobile] Creating virtual environment at .venv...
    if defined UV_EXE (
        "!UV_EXE!" venv "%VENV_DIR%"
    ) else (
        !BASE_PY! -m venv "%VENV_DIR%"
    )
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set "FORCE_INSTALL=1"
)

:: ----------------------------------------------------------------------------
:: 3. Dependency Installation & Verification
:: ----------------------------------------------------------------------------
:check_dependencies
set "NEED_INSTALL=!FORCE_INSTALL!"

if "!NEED_INSTALL!"=="0" (
    "!VENV_PY!" -c "import fastapi, uvicorn, psutil, qrcode, jwt, pydantic" >nul 2>&1
    if !ERRORLEVEL! neq 0 set "NEED_INSTALL=1"
)

if "!NEED_INSTALL!"=="1" (
    echo [AgyMobile] Installing/syncing project dependencies...
    if defined UV_EXE (
        "!UV_EXE!" pip install -e "%SCRIPT_DIR%"
    ) else (
        "!VENV_PY!" -m pip install -e "%SCRIPT_DIR%"
    )
    if !ERRORLEVEL! neq 0 (
        echo [WARNING] Primary package install returned non-zero code. Trying requirements install...
        if exist "%SCRIPT_DIR%\requirements.txt" (
            "!VENV_PY!" -m pip install -r "%SCRIPT_DIR%\requirements.txt"
        )
    )
)

:: ----------------------------------------------------------------------------
:: 4. Browser Auto-Open (Background Non-Blocking)
:: ----------------------------------------------------------------------------
if "!OPEN_BROWSER!"=="1" (
    echo [AgyMobile] Opening dashboard in default browser...
    start "" "http://localhost:%PORT%"
)

:: ----------------------------------------------------------------------------
:: 5. Launch FastAPI Backend Server
:: ----------------------------------------------------------------------------
echo [AgyMobile] Starting server on %HOST%:%PORT%...
echo [AgyMobile] (Press Ctrl+C to terminate the server at any time)
echo.

cd /d "%SCRIPT_DIR%"
"!VENV_PY!" -m uvicorn backend.main:app --host %HOST% --port %PORT% !UVICORN_ARGS!
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if %EXIT_CODE% equ 0 (
    echo [AgyMobile] Server stopped gracefully.
) else (
    echo [AgyMobile] Server exited with code %EXIT_CODE%.
    echo [INFO] If port %PORT% is already in use, check running processes: netstat -ano ^| findstr %PORT%
)

exit /b %EXIT_CODE%
