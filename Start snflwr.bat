@echo off
:: snflwr.ai Launcher — friendly name for USB root
:: Delegates to the GUI launcher or falls back to the terminal startup script

:: Resolve the directory this .bat lives in
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Try the GUI launcher with the venv pythonw (no console window)
if exist "venv\Scripts\pythonw.exe" (
    if exist "launcher\app.py" (
        start "" "venv\Scripts\pythonw.exe" "launcher\app.py"
        exit /b 0
    )
)

:: Fallback: system pythonw
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    if exist "launcher\app.py" (
        start "" pythonw launcher\app.py
        exit /b 0
    )
)

:: Last resort: terminal-based startup
if exist "START_SNFLWR.bat" (
    call START_SNFLWR.bat
    exit /b %ERRORLEVEL%
)

echo ERROR: snflwr.ai startup scripts not found.
echo Run setup first:  python install.py
pause
