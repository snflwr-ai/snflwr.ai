# snflwr.ai Startup Script (Windows)
# Starts the complete K-12 safe AI learning platform
#
# If you get "running scripts is disabled on this system", run this first:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
#
# Or use START_SNFLWR.bat instead (no execution policy needed).

param(
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

# Resolve script directory so paths work regardless of cwd
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  snflwr.ai - Startup Script (Windows)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check for Python 3.8+
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "ERROR: Python is not installed" -ForegroundColor Red
    Write-Host "Run .\setup.bat first, or install Python 3.8+ from https://www.python.org/downloads/"
    Write-Host "IMPORTANT: Check 'Add Python to PATH' during installation."
    exit 1
}

$pyVersionOk = python -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    $currentVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    Write-Host "ERROR: Python 3.8+ is required (found $currentVer)" -ForegroundColor Red
    Write-Host "Run .\setup.bat to install a supported version."
    exit 1
}

# Create required directories
New-Item -ItemType Directory -Force -Path data, logs | Out-Null

# Check if virtual environment exists
if (-not (Test-Path "venv")) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv venv
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Green
& .\venv\Scripts\Activate.ps1

# Check if dependencies are installed
$depCheck = python -c "import fastapi" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
    pip install -q -r requirements.txt
}

# Get database path from config
$DB_PATH = python -c "from config import system_config; print(system_config.DB_PATH)" 2>$null
if (-not $DB_PATH) { $DB_PATH = "data\snflwr.db" }

# Check if database exists
if (-not (Test-Path $DB_PATH)) {
    Write-Host "Initializing database..." -ForegroundColor Yellow
    python -m database.init_db
}

# Load .env file if it exists
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -and -not $_.StartsWith("#")) {
            $parts = $_ -split "=", 2
            if ($parts.Count -eq 2) {
                [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
            }
        }
    }
}

# Check if Redis is running (if enabled)
if ($env:REDIS_ENABLED -eq "true") {
    $redisRunning = $false
    try {
        $r = redis-cli ping 2>$null
        if ($r -eq "PONG") { $redisRunning = $true }
    } catch {}
    if (-not $redisRunning) {
        Write-Host "WARNING: Redis is not running" -ForegroundColor Yellow
        Write-Host "Redis is recommended for authentication rate limiting and caching."
        Write-Host "Continuing without Redis (using in-memory fallback)..."
        [Environment]::SetEnvironmentVariable("REDIS_ENABLED", "false", "Process")
        Write-Host ""
    }
} else {
    Write-Host "Redis disabled via REDIS_ENABLED=false" -ForegroundColor Yellow
}

# Check if Ollama is installed
$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaPath) {
    Write-Host "Ollama is not installed. Installing via winget..." -ForegroundColor Yellow
    $wingetPath = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetPath) {
        winget install Ollama.Ollama -e --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to install Ollama" -ForegroundColor Red
            Write-Host "Please install manually from: https://ollama.com/download/windows"
            exit 1
        }
        # Refresh PATH so ollama command is available
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        Write-Host "Ollama installed successfully" -ForegroundColor Green
    } else {
        Write-Host "ERROR: winget not found. Please install Ollama manually:" -ForegroundColor Red
        Write-Host "  https://ollama.com/download/windows"
        exit 1
    }
}

# Check if Ollama is running, start if not
$ollamaRunning = $false
try {
    $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -ErrorAction SilentlyContinue
    $ollamaRunning = $true
} catch {}

if (-not $ollamaRunning) {
    Write-Host "Ollama is not running. Starting..." -ForegroundColor Yellow

    # Try launching the Ollama app (installed via winget/installer)
    $appExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
    if (Test-Path $appExe) {
        Start-Process -FilePath $appExe -WindowStyle Hidden
    } else {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    }

    # Wait for Ollama to be ready
    Write-Host "Waiting for Ollama to be ready..."
    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -ErrorAction SilentlyContinue
            $ready = $true
            break
        } catch {}
    }

    if (-not $ready) {
        Write-Host "ERROR: Could not start Ollama" -ForegroundColor Red
        Write-Host "Please start Ollama manually from the Start Menu or run: ollama serve"
        exit 1
    }
    Write-Host "Ollama is running" -ForegroundColor Green
}

# Determine the BASE model from env or hardware detection. The user-facing
# chat model is always 'snflwr.ai', built locally below as a wrapper around
# this base.
if ($env:BASE_MODEL) {
    $chatModel = $env:BASE_MODEL
} elseif ($env:OLLAMA_DEFAULT_MODEL -and $env:OLLAMA_DEFAULT_MODEL -ne "snflwr.ai") {
    # Legacy: env held a qwen3.5 tag directly
    $chatModel = $env:OLLAMA_DEFAULT_MODEL
} else {
    # Detect RAM and recommend a base model
    $ramBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
    $ramGB = [math]::Round($ramBytes / 1GB)

    $chatModel = if ($ramGB -ge 32) { "qwen3.5:35b" }
                 elseif ($ramGB -ge 24) { "qwen3.5:27b" }
                 elseif ($ramGB -ge 8)  { "qwen3.5:9b" }
                 elseif ($ramGB -ge 6)  { "qwen3.5:4b" }
                 elseif ($ramGB -ge 4)  { "qwen3.5:2b" }
                 else                   { "qwen3.5:0.8b" }

    Write-Host "Detected $ramGB GB RAM -> base model $chatModel" -ForegroundColor Green
}

# Export so the API server (and Open WebUI) talk to the snflwr.ai wrapper
$env:BASE_MODEL = $chatModel
$env:OLLAMA_DEFAULT_MODEL = "snflwr.ai"

# Pull base model if not already available
$modelNames = (ollama list 2>&1) | ForEach-Object { ($_ -split '\s+')[0] }
if ($chatModel -notin $modelNames) {
    Write-Host "Base model '$chatModel' not found. Pulling..." -ForegroundColor Yellow
    Write-Host "This may take several minutes on the first run..."
    ollama pull $chatModel
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to pull base model '$chatModel'" -ForegroundColor Red
        Write-Host "You can retry manually: ollama pull $chatModel"
        exit 1
    }
}

# Build (or rebuild) the snflwr.ai wrapper from the modelfile, substituting
# the chosen base. This is what kids see in the Open WebUI dropdown.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$modelfileSrc = Join-Path $scriptDir "models\Snflwr_AI_Kids.modelfile"
if (Test-Path $modelfileSrc) {
    Write-Host "Building 'snflwr.ai' on top of '$chatModel'..." -ForegroundColor Green
    $tmpModelfile = [System.IO.Path]::GetTempFileName()
    (Get-Content $modelfileSrc) -replace '^FROM .*', "FROM $chatModel" | Set-Content $tmpModelfile
    ollama create snflwr.ai -f $tmpModelfile | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "'snflwr.ai' built successfully." -ForegroundColor Green
    } else {
        Write-Host "WARNING: Failed to build 'snflwr.ai' wrapper." -ForegroundColor Yellow
        Write-Host "Falling back to base model — kids will see '$chatModel' in the dropdown."
        $env:OLLAMA_DEFAULT_MODEL = $chatModel
    }
    Remove-Item $tmpModelfile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "WARNING: Modelfile not found at $modelfileSrc" -ForegroundColor Yellow
    Write-Host "Falling back to base model — kids will see '$chatModel' in the dropdown."
    $env:OLLAMA_DEFAULT_MODEL = $chatModel
}

# Pull child-safety model if enabled
if ($env:ENABLE_SAFETY_MODEL -eq "true") {
    $safetyModel = "llama-guard3:1b"
    if ($safetyModel -notin $modelNames) {
        Write-Host "Pulling child-safety model $safetyModel (~1 GB)..." -ForegroundColor Yellow
        ollama pull $safetyModel
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: Failed to pull safety model. Content filtering will use pattern-matching only." -ForegroundColor Yellow
        } else {
            Write-Host "Safety model ready." -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Prerequisites check complete" -ForegroundColor Green
Write-Host ""

# Kill any leftover API server from a previous run
$existingPids = (Get-NetTCPConnection -LocalPort 39150 -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
foreach ($pid in $existingPids) {
    if ($pid -and $pid -ne 0) {
        Write-Host "Stopping previous API server (PID: $pid)..." -ForegroundColor Yellow
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
}
if ($existingPids) { Start-Sleep -Seconds 2 }

# Start snflwr.ai API server in background
Write-Host "Starting snflwr.ai API server..." -ForegroundColor Green
$apiProcess = Start-Process -FilePath "python" -ArgumentList "-m api.server" `
    -RedirectStandardOutput "logs\api.log" -RedirectStandardError "logs\api-error.log" `
    -PassThru -WindowStyle Hidden
$apiPid = $apiProcess.Id
Write-Host "API server PID: $apiPid"

# Wait for API to start
Write-Host "Waiting for API to be ready..."
$apiReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:39150/health" -TimeoutSec 3 -ErrorAction SilentlyContinue
        $apiReady = $true
        Write-Host "API server is running" -ForegroundColor Green
        break
    } catch {}
}

if (-not $apiReady) {
    Write-Host "ERROR: API server failed to start" -ForegroundColor Red
    Write-Host "Check logs\api.log for details"
    Stop-Process -Id $apiPid -ErrorAction SilentlyContinue
    exit 1
}

# ---- Docker + Open WebUI ----

$webuiRunning = $false
$composeFile = Join-Path $ScriptDir "frontend\open-webui\docker-compose.yaml"

# Detect docker compose command
function Get-ComposeCmd {
    try {
        # Try v2 plugin first
        $null = & docker compose version 2>$null
        if ($LASTEXITCODE -eq 0) { return "v2" }
    } catch {}

    # Fall back to v1 standalone
    $v1 = Get-Command docker-compose -ErrorAction SilentlyContinue
    if ($v1) { return "v1" }

    return ""
}

$composeCmd = Get-ComposeCmd

# Helper: run docker compose with proper error handling
function Invoke-Compose {
    param([string[]]$ComposeArgs)
    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($composeCmd -eq "v2") {
            & docker compose @ComposeArgs 2>&1 | Write-Host
        } else {
            & docker-compose @ComposeArgs 2>&1 | Write-Host
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPref
    }
}

if (-not $composeCmd) {
    # Docker not found -- try to install via winget
    Write-Host ""
    Write-Host "Docker is not installed. The Open WebUI chat interface requires Docker." -ForegroundColor Yellow
    $wingetPath = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetPath) {
        Write-Host "Installing Docker Desktop via winget (this may take a few minutes)..." -ForegroundColor Yellow
        winget install Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) {
            # Refresh PATH so docker is available in this session
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            Write-Host "Docker Desktop installed successfully." -ForegroundColor Green
            Write-Host ""
            Write-Host "NOTE: Docker Desktop may require a restart to work properly." -ForegroundColor Yellow
            Write-Host "If the chat UI does not start, reboot and run this script again."
            Write-Host ""
            # Re-detect compose after install
            $composeCmd = Get-ComposeCmd
            if (-not $composeCmd) {
                Write-Host "Docker is installed but not yet on the PATH." -ForegroundColor Yellow
                Write-Host "Please restart your computer, then run this script again."
                Write-Host "Continuing without Open WebUI frontend for now."
                Write-Host ""
            }
        } else {
            Write-Host "WARNING: Failed to install Docker Desktop via winget." -ForegroundColor Yellow
            Write-Host "Install manually from: https://www.docker.com/products/docker-desktop/"
            Write-Host "Continuing without Open WebUI frontend."
            Write-Host ""
        }
    } else {
        Write-Host "winget is not available -- cannot install Docker automatically."
        Write-Host "Install Docker Desktop manually from: https://www.docker.com/products/docker-desktop/"
        Write-Host "Continuing without Open WebUI frontend."
        Write-Host ""
    }
}

if ($composeCmd) {
    # Ensure Docker daemon is running
    $dockerOk = $false
    try {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
    } catch {}

    if (-not $dockerOk) {
        Write-Host "Docker daemon is not running. Trying to start Docker Desktop..." -ForegroundColor Yellow
        # Check both common install locations
        $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        $dockerDesktopLocal = Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe"
        if (Test-Path $dockerDesktop) {
            Start-Process -FilePath $dockerDesktop -WindowStyle Minimized
        } elseif (Test-Path $dockerDesktopLocal) {
            Start-Process -FilePath $dockerDesktopLocal -WindowStyle Minimized
        }
        Write-Host "Waiting for Docker to start..."
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            try {
                docker info 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break }
            } catch {}
        }
    }

    if (-not $dockerOk) {
        Write-Host "WARNING: Could not start Docker daemon" -ForegroundColor Yellow
        Write-Host "Continuing without Open WebUI frontend."
        Write-Host "Start Docker Desktop manually and re-run to get the chat UI."
        Write-Host ""
    } elseif (-not (Test-Path $composeFile)) {
        Write-Host "WARNING: Open WebUI compose file not found at $composeFile" -ForegroundColor Yellow
        Write-Host "Continuing without Open WebUI frontend."
        Write-Host ""
    } else {
        Write-Host "Docker is running" -ForegroundColor Green

        # Remove stale open-webui container from a previous run
        docker rm -f open-webui 2>$null | Out-Null

        # Kill anything already occupying port 3000
        $port3000Pids = (Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
        foreach ($pid in $port3000Pids) {
            if ($pid -and $pid -ne 0) {
                Write-Host "Port 3000 is already in use (PID: $pid) - stopping..." -ForegroundColor Yellow
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            }
        }
        if ($port3000Pids) { Start-Sleep -Seconds 2 }

        # Pull the Open WebUI image first (shows download progress on first run)
        Write-Host ""

        # Check if image is already cached locally
        $webuiTag = if ($env:WEBUI_DOCKER_TAG) { $env:WEBUI_DOCKER_TAG } else { "v0.8.3" }
        $webuiImage = "ghcr.io/open-webui/open-webui:$webuiTag"
        $imageCached = $false
        try {
            docker image inspect $webuiImage 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $imageCached = $true }
        } catch {}

        if ($imageCached) {
            Write-Host "Open WebUI image already cached locally, skipping pull." -ForegroundColor Green
        } else {
            Write-Host "Pulling Open WebUI Docker image (this may take a few minutes on first run)..." -ForegroundColor Green
            $pullRc = Invoke-Compose -ComposeArgs @("-f", $composeFile, "pull")
            if ($pullRc -ne 0) {
                Write-Host "WARNING: Failed to pull Open WebUI image" -ForegroundColor Yellow
                Write-Host "Will try to start with cached image (if available)..."
            }
        }

        # Start Open WebUI via Docker Compose (retry once on failure)
        Write-Host "Starting Open WebUI frontend..." -ForegroundColor Green
        $composeOk = $false
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            $composeRc = Invoke-Compose -ComposeArgs @("-f", $composeFile, "up", "-d")
            if ($composeRc -eq 0) {
                $composeOk = $true
                break
            }
            if ($attempt -eq 1) {
                Write-Host "  Compose attempt 1 failed, retrying..." -ForegroundColor Yellow
                Start-Sleep -Seconds 3
            }
        }

        if (-not $composeOk) {
            Write-Host "WARNING: Failed to start Open WebUI via Docker" -ForegroundColor Yellow
            Write-Host "Try running manually: docker compose -f `"$composeFile`" up -d"
            Write-Host "Continuing with API server only."
            Write-Host ""
        } else {
            # Wait for Open WebUI to be ready (up to ~120 s on first run)
            Write-Host "Waiting for Open WebUI to be ready..."
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 2
                try {
                    $response = Invoke-WebRequest -Uri "http://localhost:3000" -TimeoutSec 3 -ErrorAction SilentlyContinue
                    $webuiRunning = $true
                    Write-Host "Open WebUI is running" -ForegroundColor Green
                    break
                } catch {}

                # Check if container exited unexpectedly (at 20s and 60s)
                if ($i -eq 10 -or $i -eq 30) {
                    try {
                        $containerStatus = (docker inspect -f '{{.State.Status}}' open-webui 2>$null)
                        if ($containerStatus -eq "exited" -or $containerStatus -eq "dead") {
                            Write-Host "  Container exited - restarting..." -ForegroundColor Yellow
                            $null = Invoke-Compose -ComposeArgs @("-f", $composeFile, "up", "-d")
                        }
                    } catch {}
                }
            }

            if (-not $webuiRunning) {
                Write-Host "Open WebUI is starting up (may take a minute on first run)" -ForegroundColor Yellow
                $webuiRunning = $true  # Container started, just slow
            }
        }
    }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  snflwr.ai is running!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

if ($webuiRunning) {
    Write-Host "  Chat UI:  http://localhost:3000" -ForegroundColor Green
} else {
    Write-Host "  Chat UI:  not available (Docker required)" -ForegroundColor Yellow
}
Write-Host "  API:      http://localhost:39150"
Write-Host "  API Docs: http://localhost:39150/docs"
Write-Host "  Logs:     $ScriptDir\logs\api.log"
Write-Host ""
Write-Host "To stop:"
Write-Host "  Press Ctrl+C"
Write-Host ""

# Open browser -- chat UI if available, otherwise API docs
if ($webuiRunning) {
    Start-Process "http://localhost:3000"
} else {
    Start-Process "http://localhost:39150/docs"
}

# Keep script running -- cleanup on exit
try {
    if ($Headless) {
        Write-Host "Running in headless mode. PID: $PID"
        # Wait indefinitely -- GUI launcher will kill this process
        while ($true) { Start-Sleep -Seconds 3600 }
    } else {
        Get-Content "logs\api.log" -Wait
    }
} finally {
    Write-Host "Stopping services..." -ForegroundColor Yellow
    Stop-Process -Id $apiPid -ErrorAction SilentlyContinue
    if ($webuiRunning) {
        try { $null = Invoke-Compose -ComposeArgs @("-f", $composeFile, "down") } catch {}
    }
    Write-Host "Server stopped." -ForegroundColor Green
}
