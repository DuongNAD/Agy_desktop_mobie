@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: AgyMobile - 1-Click Complete Installer & Desktop Shortcut for Windows
:: ============================================================================

title AgyMobile - 1-Click Installer
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo ==============================================================================
echo   AgyMobile - Bo Cai Dat 1-Click Cho May Tinh Windows
echo   Antigravity 2.0 & Desktop Workspace Mobile Remote Platform
echo ==============================================================================
echo.

:: 1. Check & Install Dependencies via start.bat
echo [1/3] Dang kiem tra moi truong Python va cai dat goi thu vien...
call "%~dp0start.bat" --install --no-browser

:: 2. Create Desktop Shortcut
echo.
echo [2/3] Dang tao bieu tuong loi tat (Desktop Shortcut)...
set "DESKTOP_DIR=%USERPROFILE%\Desktop"
set "TARGET_BAT=%~dp0start.bat"
set "SHORTCUT_PATH=%DESKTOP_DIR%\AgyMobile.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'AgyMobile - Antigravity Desktop & Mobile Remote Platform'; $s.Save()" >nul 2>&1

if exist "%SHORTCUT_PATH%" (
    echo [AgyMobile] Da tao thanh cong loi tat AgyMobile tren Desktop!
) else (
    echo [AgyMobile] Ban co the khoi dong bang cach click dup vao start.bat
)

:: 3. Finish and Launch
echo.
echo [3/3] Hoan tat cai dat! Dang khoi dong AgyMobile...
echo ==============================================================================
echo.
start "" "%~dp0start.bat"
exit /b 0
