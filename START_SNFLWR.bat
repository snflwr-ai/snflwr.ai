@echo off
setlocal enabledelayedexpansion

:: Resolve script directory so paths work regardless of cwd
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Check for headless mode (used by GUI launcher)
set "HEADLESS=0"
if /i "%~1"=="/headless" set "HEADLESS=1"
if /i "%~1"=="--headless" set "HEADLESS=1"

echo ==========================================
echo   snflwr.ai - Startup Script (Windows)
echo ==========================================
echo.

:: Check for Python
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed.
    echo Run setup.bat first, or install Python 3.8+ from https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Verify Python 3.8+
python -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python 3.8+ is required.
    echo Run setup.bat to install a supported version.
    pause
    exit /b 1
)

:: Create required directories
if not exist "data" mkdir data
if not exist "logs" mkdir logs

:: Create virtual environment if needed
if not exist "venv" (
    echo Creating Python virtual environment...
    python -m venv venv
)

:: Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

:: Check if dependencies are installed
python -c "import fastapi" 2>nul
if %ERRORLEVEL% neq 0 (
    echo Installing Python dependencies...
    pip install -q -r requirements.txt
)

:: Initialize database if needed
for /f "delims=" %%i in ('python -c "from config import system_config; print(system_config.DB_PATH)" 2^>nul') do set DB_PATH=%%i
if "%DB_PATH%"=="" set DB_PATH=data\snflwr.db

if not exist "%DB_PATH%" (
    echo Initializing database...
    python -m database.init_db
    if !ERRORLEVEL! neq 0 (
        echo ERROR: Database initialization failed.
        echo Check the output above for details.
        pause
        exit /b 1
    )
)

:: Load .env file if it exists
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            if not "%%b"=="" set "%%a=%%b"
        )
    )
)

:: Check if Redis is running (if enabled)
if "!REDIS_ENABLED!"=="true" (
    redis-cli ping >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo WARNING: Redis is not running.
        echo Continuing without Redis (using in-memory fallback^)...
        set "REDIS_ENABLED=false"
        echo.
    )
) else (
    if defined REDIS_ENABLED (
        echo Redis disabled via REDIS_ENABLED=false
    )
)

:: Check if Ollama is installed
where ollama >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Ollama is not installed. Attempting install via winget...
    where winget >nul 2>&1
    if !ERRORLEVEL!==0 (
        winget install Ollama.Ollama -e --accept-source-agreements
        if !ERRORLEVEL! neq 0 (
            echo WARNING: Failed to install Ollama automatically.
            echo Install from: https://ollama.com/download/windows
            echo AI features will not work until Ollama is installed.
            echo.
            goto :start_api
        )
        :: Refresh PATH from registry so ollama command is available
        for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
        for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USR_PATH=%%b"
        set "PATH=!SYS_PATH!;!USR_PATH!"
        echo Ollama installed successfully.
    ) else (
        echo WARNING: Ollama is not installed and winget is not available.
        echo Install from: https://ollama.com/download/windows
        echo AI features will not work until Ollama is installed.
        echo.
        goto :start_api
    )
)

:: Check if Ollama is running
set "OLLAMA_STARTED=0"
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Starting Ollama...
    set "OLLAMA_STARTED=1"

    :: Try the Ollama app first
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
        start "" /min "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
    ) else (
        start "" /min ollama serve
    )

    :: Wait for Ollama to be ready
    echo Waiting for Ollama to be ready...
    set READY=0
    for /l %%i in (1,1,15) do (
        if !READY!==0 (
            timeout /t 2 /nobreak >nul
            curl -s http://localhost:11434/api/tags >nul 2>&1
            if !ERRORLEVEL!==0 set READY=1
        )
    )

    if !READY!==0 (
        echo WARNING: Could not start Ollama. Start it manually from the Start Menu.
        echo Continuing with API server startup...
    ) else (
        echo Ollama is running.
    )
)

:: Determine chat model from env or hardware detection
if "%OLLAMA_DEFAULT_MODEL%"=="" (
    :: Detect RAM via PowerShell (avoids 32-bit overflow in set /a)
    for /f %%G in ('powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)"') do (
        set "RAM_GB=%%G"
    )
    if !RAM_GB! GEQ 32 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:35b
    ) else if !RAM_GB! GEQ 24 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:27b
    ) else if !RAM_GB! GEQ 8 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:9b
    ) else if !RAM_GB! GEQ 6 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:4b
    ) else if !RAM_GB! GEQ 4 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:2b
    ) else ( set OLLAMA_DEFAULT_MODEL=qwen3.5:0.8b
    )
    echo Detected !RAM_GB! GB RAM -- recommending !OLLAMA_DEFAULT_MODEL!
)
ollama list 2>nul | findstr /b "%OLLAMA_DEFAULT_MODEL%" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Pulling model %OLLAMA_DEFAULT_MODEL% (this may take several minutes^)...
    ollama pull %OLLAMA_DEFAULT_MODEL%
)

:: Pull child-safety model if enabled
if /i "!ENABLE_SAFETY_MODEL!"=="true" (
    ollama list 2>nul | findstr /b "llama-guard3:1b" >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo Pulling child-safety model llama-guard3:1b (~1 GB^)...
        ollama pull llama-guard3:1b
        if !ERRORLEVEL! neq 0 (
            echo WARNING: Failed to pull safety model. Content filtering will use pattern-matching only.
        ) else (
            echo Safety model ready.
        )
    )
)

:start_api
echo.

:: Kill any leftover API server from a previous run
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":39150.*LISTENING"') do (
    echo Stopping previous API server (PID: %%p^)...
    taskkill /f /pid %%p >nul 2>&1
)

echo Starting snflwr.ai API server...
start "snflwr.ai API" /min cmd /c "python -m api.server > logs\api.log 2>&1"

:: Wait for API to start
echo Waiting for API to be ready...
set API_READY=0
for /l %%i in (1,1,30) do (
    if !API_READY!==0 (
        timeout /t 1 /nobreak >nul
        curl -s http://localhost:39150/health >nul 2>&1
        if !ERRORLEVEL!==0 set API_READY=1
    )
)

if !API_READY!==0 (
    echo ERROR: API server failed to start.
    echo Check logs\api.log for details.
    pause
    goto :shutdown
)

:: ---- Docker + Open WebUI ----

:: Track whether Open WebUI is running
set "WEBUI_RUNNING=0"
set "COMPOSE_CMD="
set "COMPOSE_FILE=%SCRIPT_DIR%frontend\open-webui\docker-compose.yaml"

:: Detect Docker Compose command
docker compose version >nul 2>&1
if %ERRORLEVEL%==0 (
    set "COMPOSE_CMD=docker compose"
    goto :compose_found
)
where docker-compose >nul 2>&1
if %ERRORLEVEL%==0 (
    set "COMPOSE_CMD=docker-compose"
    goto :compose_found
)

:: Docker not found — try to install via winget
echo.
echo Docker is not installed. The Open WebUI chat interface requires Docker.
where winget >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo winget is not available — cannot install Docker automatically.
    echo Install Docker Desktop manually from: https://www.docker.com/products/docker-desktop/
    echo Continuing without Open WebUI frontend.
    echo.
    goto :running
)

echo Installing Docker Desktop via winget (this may take a few minutes^)...
winget install Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements
if !ERRORLEVEL! neq 0 (
    echo WARNING: Failed to install Docker Desktop via winget.
    echo Install manually from: https://www.docker.com/products/docker-desktop/
    echo Continuing without Open WebUI frontend.
    echo.
    goto :running
)

:: Refresh PATH from registry so docker is available in this session
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USR_PATH=%%b"
set "PATH=!SYS_PATH!;!USR_PATH!"

:: Verify docker is now available
docker compose version >nul 2>&1
if %ERRORLEVEL%==0 (
    set "COMPOSE_CMD=docker compose"
    echo Docker Desktop installed successfully.
    echo.
    echo NOTE: Docker Desktop may require a restart to work properly.
    echo If the chat UI does not start, reboot and run this script again.
    echo.
) else (
    echo Docker Desktop was installed but is not yet on the PATH.
    echo Please restart your computer, then run this script again.
    echo Continuing without Open WebUI frontend for now.
    echo.
    goto :running
)

:compose_found
:: Verify Docker daemon is running
docker info >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Docker daemon is not running. Trying to start Docker Desktop...
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
        start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    ) else if exist "%LOCALAPPDATA%\Docker\Docker Desktop.exe" (
        start "" "%LOCALAPPDATA%\Docker\Docker Desktop.exe"
    )
    echo Waiting for Docker to start...
    set "DOCKER_OK=0"
    for /l %%i in (1,1,30) do (
        if !DOCKER_OK!==0 (
            timeout /t 2 /nobreak >nul
            docker info >nul 2>&1
            if !ERRORLEVEL!==0 set "DOCKER_OK=1"
        )
    )
    if !DOCKER_OK!==0 (
        echo WARNING: Docker Desktop did not start in time.
        echo Continuing without Open WebUI frontend.
        echo Start Docker Desktop manually and re-run to get the chat UI.
        echo.
        goto :running
    )
)

echo Docker is running.

:: Start Open WebUI
if not exist "%COMPOSE_FILE%" (
    echo WARNING: Open WebUI compose file not found at %COMPOSE_FILE%
    echo Continuing without Open WebUI frontend.
    echo.
    goto :running
)

:: Remove stale open-webui container from a previous run
docker rm -f open-webui >nul 2>&1

:: Kill anything already occupying port 3000 (exact match — avoid hitting 30000+)
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr /c:":3000 " ^| findstr LISTENING') do (
    echo Port 3000 is already in use (PID: %%p^) - stopping...
    taskkill /f /pid %%p >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo.

:: Check if the Open WebUI image already exists locally
set "WEBUI_IMAGE=ghcr.io/open-webui/open-webui:%WEBUI_DOCKER_TAG%"
if "%WEBUI_DOCKER_TAG%"=="" set "WEBUI_IMAGE=ghcr.io/open-webui/open-webui:v0.8.12"
docker image inspect %WEBUI_IMAGE% >nul 2>&1
if %ERRORLEVEL%==0 (
    echo Open WebUI image already cached locally, skipping pull.
) else (
    echo Pulling Open WebUI Docker image (this may take a few minutes on first run^)...
    set "PULL_OK=0"
    for /l %%a in (1,1,3) do (
        if !PULL_OK!==0 (
            %COMPOSE_CMD% -f "%COMPOSE_FILE%" pull >nul 2>&1
            if !ERRORLEVEL!==0 (
                set "PULL_OK=1"
            ) else (
                if %%a lss 3 (
                    set /a "WAIT=%%a*2"
                    echo   Pull attempt %%a failed, retrying in !WAIT! seconds...
                    timeout /t !WAIT! /nobreak >nul
                )
            )
        )
    )
    if !PULL_OK!==0 (
        echo WARNING: Failed to pull Open WebUI image after 3 attempts.
        echo Will try to start with cached image (if available^)...
    ) else (
        echo Open WebUI image pulled successfully.
    )
)

:: Start Open WebUI via Docker Compose (retry once on failure)
echo Starting Open WebUI frontend...
set "COMPOSE_OK=0"
for /l %%a in (1,1,2) do (
    if !COMPOSE_OK!==0 (
        %COMPOSE_CMD% -f "%COMPOSE_FILE%" up -d >nul 2>&1
        if !ERRORLEVEL!==0 (
            set "COMPOSE_OK=1"
        ) else (
            if %%a==1 (
                echo   Compose attempt 1 failed, retrying...
                timeout /t 3 /nobreak >nul
            )
        )
    )
)

if !COMPOSE_OK!==0 (
    echo WARNING: Failed to start Open WebUI via Docker.
    echo Try running manually: %COMPOSE_CMD% -f "%COMPOSE_FILE%" up -d
    echo Continuing with API server only.
    echo.
    goto :running
)

:: Wait for Open WebUI to be ready (up to ~120 s on first run)
echo Waiting for Open WebUI to be ready...
set WEBUI_READY=0
set WEBUI_CHECK=0
for /l %%i in (1,1,60) do (
    if !WEBUI_READY!==0 (
        timeout /t 2 /nobreak >nul
        curl -s http://localhost:3000 >nul 2>&1
        if !ERRORLEVEL!==0 (
            set WEBUI_READY=1
        ) else (
            set /a "WEBUI_CHECK=%%i"
            if !WEBUI_CHECK!==10 (
                docker inspect -f "{{.State.Status}}" open-webui 2>nul | findstr /i "exited dead" >nul 2>&1
                if !ERRORLEVEL!==0 (
                    echo   Container exited - restarting...
                    %COMPOSE_CMD% -f "%COMPOSE_FILE%" up -d >nul 2>&1
                )
            )
            if !WEBUI_CHECK!==30 (
                docker inspect -f "{{.State.Status}}" open-webui 2>nul | findstr /i "exited dead" >nul 2>&1
                if !ERRORLEVEL!==0 (
                    echo   Container exited - restarting...
                    %COMPOSE_CMD% -f "%COMPOSE_FILE%" up -d >nul 2>&1
                )
            )
        )
    )
)

if !WEBUI_READY!==0 (
    echo Open WebUI is starting up (may take a minute on first run^)...
) else (
    echo Open WebUI is running.
)
if !WEBUI_READY!==1 (
    set "WEBUI_RUNNING=1"
) else (
    :: Container started but not yet responding — check if it's at least running
    docker inspect -f "{{.State.Status}}" open-webui 2>nul | findstr /i "running" >nul 2>&1
    if !ERRORLEVEL!==0 (
        set "WEBUI_RUNNING=1"
    )
)

:running
echo.
echo ==========================================
echo   snflwr.ai is running!
echo ==========================================
echo.

if "!WEBUI_RUNNING!"=="1" (
    echo   Chat UI:  http://localhost:3000
) else (
    echo   Chat UI:  not available (Docker required^)
)
echo   Admin:    http://localhost:39150/admin
echo   API Docs: http://localhost:39150/docs
echo   Logs:     %SCRIPT_DIR%logs\api.log
echo.
echo To stop:
echo   Press any key in this window
echo.

if "%HEADLESS%"=="1" goto :headless_mode

:: Interactive (terminal) mode — open browser
if "!WEBUI_RUNNING!"=="1" (
    start "" http://localhost:3000
) else (
    start "" http://localhost:39150/docs
)
echo Press any key to stop the server...
pause >nul
goto :shutdown

:headless_mode
echo Running in headless mode.
:: GUI launcher manages the lifecycle and has its own "Open in Browser"
:: button — don't auto-open a tab the launcher can't close on stop.
:: Loop with short timeouts so Ctrl+C followed by N triggers clean shutdown.
:headless_wait
timeout /t 5 /nobreak >nul 2>&1
if !ERRORLEVEL!==0 goto :headless_wait

:shutdown
echo Stopping services...

:: Kill the API server by port (reliable) instead of window title (fragile).
:: /t kills the entire process tree (cmd.exe wrapper + python + uvicorn workers).
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":39150.*LISTENING"') do (
    taskkill /f /t /pid %%p >nul 2>&1
)

if "!WEBUI_RUNNING!"=="1" (
    %COMPOSE_CMD% -f "%COMPOSE_FILE%" down 2>nul
)

:: Stop Ollama only if this script started it
if "!OLLAMA_STARTED!"=="1" (
    taskkill /f /im ollama.exe >nul 2>&1
    taskkill /f /im "ollama app.exe" >nul 2>&1
)

echo Server stopped.
