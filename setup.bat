@echo off
REM snflwr.ai Bootstrap Setup (Windows)
REM Ensures Python 3 is installed, then runs the interactive installer.

echo ==========================================
echo   snflwr.ai - Bootstrap Setup
echo ==========================================
echo.

REM ---------------------------------------------------------------------------
REM Check for Python 3.8+
REM ---------------------------------------------------------------------------
where python >nul 2>&1
if %errorlevel% neq 0 goto :install_python

python -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" 2>nul
if %errorlevel% neq 0 goto :install_python

goto :python_ready

:install_python
echo Python 3.8+ is required but not found.
echo.

REM Try winget first (Windows 10 1709+ / Windows 11)
where winget >nul 2>&1
if %errorlevel% neq 0 goto :no_winget

echo Installing Python via winget...
winget install Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
if %errorlevel% neq 0 (
    echo ERROR: Failed to install Python via winget.
    goto :manual_install
)

REM Refresh PATH so python is found
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"

REM Verify
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python was installed but is not on PATH.
    echo Please close and reopen this terminal, then run setup.bat again.
    pause
    exit /b 1
)

echo Python installed successfully.
goto :python_ready

:no_winget
REM Try Microsoft Store via start command
echo winget not found. Attempting Microsoft Store install...
echo.
echo If the Microsoft Store opens, please install Python 3.12 and then
echo close and reopen this terminal and run setup.bat again.
echo.
start ms-windows-store://pdp/?productid=9NCVDN91XZQP
goto :manual_install

:manual_install
echo.
echo Please install Python 3.8+ manually from:
echo   https://www.python.org/downloads/
echo.
echo IMPORTANT: During installation, check "Add Python to PATH"
echo.
echo After installing, close and reopen this terminal, then run setup.bat again.
pause
exit /b 1

:python_ready
for /f "tokens=*" %%i in ('python -c "import sys; print(sys.version.split()[0])"') do set PYTHON_VERSION=%%i
echo Using Python %PYTHON_VERSION%

REM Ensure pip is available
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing pip...
    python -m ensurepip --upgrade
)

echo.
echo Python is ready. Launching snflwr.ai installer...
echo.

REM Change to the script's directory
cd /d "%~dp0"

python install.py %*
