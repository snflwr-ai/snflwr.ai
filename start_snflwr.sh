#!/bin/bash

# snflwr.ai Startup Script
# Starts the complete K-12 safe AI learning platform

set -e

# Resolve absolute path to the repo directory (works even when called from elsewhere)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Headless mode: skip tail at the end (used by GUI launcher)
HEADLESS=false
if [ "$1" = "--headless" ]; then
    HEADLESS=true
fi

echo "=========================================="
echo "  snflwr.ai - Startup Script"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Detect docker compose command (v2 plugin vs v1 standalone)
detect_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose &>/dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# Open a URL in the default browser (cross-platform)
open_browser() {
    local url="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        open "$url" 2>/dev/null || true
    elif grep -qEi "(Microsoft|WSL)" /proc/version 2>/dev/null; then
        # WSL (Windows Subsystem for Linux)
        if command -v wslview &>/dev/null; then
            wslview "$url" 2>/dev/null &
        elif command -v powershell.exe &>/dev/null; then
            powershell.exe -c "Start-Process '$url'" 2>/dev/null &
        elif command -v cmd.exe &>/dev/null; then
            cmd.exe /c "start \"\" \"$url\"" 2>/dev/null &
        fi
    elif command -v xdg-open &>/dev/null; then
        xdg-open "$url" 2>/dev/null &
    elif command -v sensible-browser &>/dev/null; then
        sensible-browser "$url" 2>/dev/null &
    elif command -v gnome-open &>/dev/null; then
        gnome-open "$url" 2>/dev/null &
    elif command -v firefox &>/dev/null; then
        firefox "$url" 2>/dev/null &
    elif command -v google-chrome &>/dev/null; then
        google-chrome "$url" 2>/dev/null &
    elif command -v chromium-browser &>/dev/null; then
        chromium-browser "$url" 2>/dev/null &
    elif command -v chromium &>/dev/null; then
        chromium "$url" 2>/dev/null &
    else
        # Last resort: Python's webbrowser module (always available in this project)
        python3 -c "import sys, webbrowser; webbrowser.open(sys.argv[1])" "$url" 2>/dev/null &
    fi
}

# Check for Python 3.8+
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}ERROR: Python 3 is not installed${NC}"
    echo "Run ./setup.sh first, or install Python 3.8+ manually:"
    echo "  Linux:  sudo apt install python3 python3-venv python3-pip"
    echo "  macOS:  brew install python@3"
    exit 1
fi

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" 2>/dev/null; then
    echo -e "${RED}ERROR: Python 3.8+ is required${NC}"
    CURRENT_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
    echo "Current version: $CURRENT_VER"
    echo "Run ./setup.sh to install a supported version."
    exit 1
fi

# Create required directories
mkdir -p data logs

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo -e "${GREEN}Activating virtual environment...${NC}"
source venv/bin/activate

# Check if dependencies are installed
if ! python -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}Installing Python dependencies...${NC}"
    pip install -q -r requirements.txt
fi

# Get database path from config (let Python handle directory creation)
DB_PATH=$(python -c "from config import system_config; print(system_config.DB_PATH)" 2>/dev/null || echo "data/snflwr.db")

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    echo -e "${YELLOW}Initializing database...${NC}"
    if ! python -m database.init_db; then
        echo -e "${RED}ERROR: Database initialization failed${NC}"
        echo "Check the output above for details."
        exit 1
    fi
fi

# Load .env file if it exists
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# Check if Redis is running (required for authentication rate limiting)
if [ "${REDIS_ENABLED:-false}" = "true" ]; then
    if ! redis-cli ping >/dev/null 2>&1; then
        echo -e "${YELLOW}WARNING: Redis is not running${NC}"
        echo "Redis is recommended for authentication rate limiting and caching."
        echo ""
        echo "To start Redis:"
        echo "  - Linux: sudo systemctl start redis"
        echo "  - macOS: brew services start redis"
        echo "  - Manual: redis-server --daemonize yes"
        echo ""
        echo "Continuing without Redis (using in-memory fallback)..."
        echo "Set REDIS_ENABLED=false in .env to suppress this warning."
        export REDIS_ENABLED=false
        echo ""
    fi
else
    echo -e "${YELLOW}Redis disabled via REDIS_ENABLED=false${NC}"
fi

# Check if Ollama is installed
if ! command -v ollama &>/dev/null; then
    echo -e "${YELLOW}Ollama is not installed. Installing...${NC}"
    if [[ "$(uname)" == "Linux" ]] || [[ "$(uname)" == "Darwin" ]]; then
        if curl -fsSL https://ollama.com/install.sh | sh; then
            echo -e "${GREEN}Ollama installed successfully${NC}"
        else
            echo -e "${RED}ERROR: Failed to install Ollama${NC}"
            echo "Please install manually from: https://ollama.com/download"
            exit 1
        fi
    else
        echo -e "${RED}ERROR: Automatic Ollama installation not supported on this platform${NC}"
        echo "Please install from: https://ollama.com/download"
        exit 1
    fi
fi

# Detect NVIDIA GPU (Ollama auto-uses GPU when running natively; this is informational)
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
    echo -e "${GREEN}GPU detected: ${GPU_NAME} — Ollama will use GPU acceleration automatically.${NC}"
else
    echo -e "${YELLOW}No NVIDIA GPU detected — Ollama will use CPU inference.${NC}"
fi

# Bind Ollama to all interfaces so Docker containers can reach it
export OLLAMA_HOST=0.0.0.0
# Explicit client URL so the snflwr API uses the correct scheme+port
# (OLLAMA_HOST above is the bind address; OLLAMA_BASE_URL is the client URL)
export OLLAMA_BASE_URL=http://localhost:11434

# Check if Ollama is running, start if not
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo -e "${YELLOW}Ollama is not running. Starting...${NC}"

    # Try platform-specific start methods
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: try launching the Ollama app
        if [ -d "/Applications/Ollama.app" ]; then
            open -a Ollama 2>/dev/null || true
            sleep 3
        fi
    elif command -v systemctl &>/dev/null; then
        # Linux: try systemctl
        systemctl start ollama 2>/dev/null || true
        sleep 2
    fi

    # If still not running, start directly in background
    if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        ollama serve >/dev/null 2>&1 &
        OLLAMA_PID=$!
        echo "Started Ollama (PID: $OLLAMA_PID)"

        # Wait for Ollama to be ready
        echo "Waiting for Ollama to be ready..."
        for i in {1..15}; do
            if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
                break
            fi
            sleep 2
        done
    fi

    if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo -e "${RED}ERROR: Could not start Ollama${NC}"
        echo "Please start manually: ollama serve"
        exit 1
    fi
    echo -e "${GREEN}Ollama is running${NC}"
fi

# Verify at least one model is available (Ollama responding != model loaded)
LOADED_MODELS=$(curl -sf http://localhost:11434/api/tags 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('models',[])))" 2>/dev/null || echo "0")
if [ "$LOADED_MODELS" = "0" ]; then
    echo -e "${YELLOW}No models loaded in Ollama yet — a model will be pulled below.${NC}"
fi

# Determine the BASE model — prefer env var (BASE_MODEL or legacy
# OLLAMA_DEFAULT_MODEL pointing at a qwen3.5 tag), otherwise detect from
# hardware. The user-facing chat model is always 'snflwr.ai', built locally
# below as a wrapper around this base.
if [ -n "$BASE_MODEL" ]; then
    CHAT_MODEL="$BASE_MODEL"
elif [ -n "$OLLAMA_DEFAULT_MODEL" ] && [ "$OLLAMA_DEFAULT_MODEL" != "snflwr.ai" ]; then
    # Legacy: env held a qwen3.5 tag directly
    CHAT_MODEL="$OLLAMA_DEFAULT_MODEL"
else
    # Detect RAM and recommend a base model
    if [ -f /proc/meminfo ]; then
        ram_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
        ram_gb=$(( (ram_kb + 524288) / 1024 / 1024 ))
    elif command -v sysctl >/dev/null 2>&1; then
        ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
        ram_gb=$(( (ram_bytes + 536870912) / 1024 / 1024 / 1024 ))
    else
        ram_gb=0
    fi

    if [ "$ram_gb" -ge 32 ]; then
        CHAT_MODEL="qwen3.5:35b"
    elif [ "$ram_gb" -ge 24 ]; then
        CHAT_MODEL="qwen3.5:27b"
    elif [ "$ram_gb" -ge 8 ]; then
        CHAT_MODEL="qwen3.5:9b"
    elif [ "$ram_gb" -ge 6 ]; then
        CHAT_MODEL="qwen3.5:4b"
    elif [ "$ram_gb" -ge 4 ]; then
        CHAT_MODEL="qwen3.5:2b"
    else
        CHAT_MODEL="qwen3.5:0.8b"
    fi

    if [ "$ram_gb" -gt 0 ]; then
        echo -e "${GREEN}Detected ${ram_gb} GB RAM → base model ${CHAT_MODEL}${NC}"
    else
        echo -e "${YELLOW}Could not detect RAM → base model ${CHAT_MODEL}${NC}"
    fi
fi

# Export so the API server (and Open WebUI) talk to the snflwr.ai wrapper
# (built below). BASE_MODEL is preserved for downstream visibility.
export BASE_MODEL="$CHAT_MODEL"
export OLLAMA_DEFAULT_MODEL="snflwr.ai"

# Pull base model if not already available
if ! ollama list | awk '{print $1}' | grep -qxF "$CHAT_MODEL"; then
    echo -e "${YELLOW}Base model '$CHAT_MODEL' not found. Pulling...${NC}"
    echo "This may take several minutes on the first run..."
    if ! ollama pull "$CHAT_MODEL"; then
        echo -e "${YELLOW}WARNING: Failed to pull base model '$CHAT_MODEL'${NC}"
        echo "You can retry manually: ollama pull $CHAT_MODEL"
        echo "The API server will still start — AI chat will not work until a model is available."
    fi
fi

# Build (or rebuild) the snflwr.ai wrapper. This is what kids see in the
# chat dropdown — the K-12 STEM tutor system prompt + sampling parameters
# from models/Snflwr_AI_Kids.modelfile, on top of the base model above.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELFILE_SRC="${SCRIPT_DIR}/models/Snflwr_AI_Kids.modelfile"
if [ -f "$MODELFILE_SRC" ]; then
    echo -e "${GREEN}Building 'snflwr.ai' on top of '${CHAT_MODEL}'...${NC}"
    TMP_MODELFILE="$(mktemp)"
    sed "s|^FROM .*|FROM ${CHAT_MODEL}|" "$MODELFILE_SRC" > "$TMP_MODELFILE"
    if ollama create snflwr.ai -f "$TMP_MODELFILE" >/dev/null 2>&1; then
        echo -e "${GREEN}'snflwr.ai' built successfully.${NC}"
    else
        echo -e "${YELLOW}WARNING: Failed to build 'snflwr.ai' wrapper.${NC}"
        echo "Falling back to base model — kids will see '${CHAT_MODEL}' in the dropdown."
        export OLLAMA_DEFAULT_MODEL="$CHAT_MODEL"
    fi
    rm -f "$TMP_MODELFILE"
else
    echo -e "${YELLOW}WARNING: Modelfile not found at $MODELFILE_SRC${NC}"
    echo "Falling back to base model — kids will see '${CHAT_MODEL}' in the dropdown."
    export OLLAMA_DEFAULT_MODEL="$CHAT_MODEL"
fi

# Pull child-safety model if enabled
if [ "${ENABLE_SAFETY_MODEL:-false}" = "true" ]; then
    SAFETY_MODEL="llama-guard3:1b"
    if ! ollama list | awk '{print $1}' | grep -qxF "$SAFETY_MODEL"; then
        echo -e "${YELLOW}Pulling child-safety model $SAFETY_MODEL (~1 GB)...${NC}"
        if ollama pull "$SAFETY_MODEL"; then
            echo -e "${GREEN}Safety model ready.${NC}"
        else
            echo -e "${YELLOW}WARNING: Failed to pull safety model. Content filtering will use pattern-matching only.${NC}"
        fi
    fi
fi

echo ""
echo -e "${GREEN}[OK] Prerequisites check complete${NC}"
echo ""

# Kill any leftover API server from a previous run
EXISTING_PIDS=$(lsof -ti :39150 2>/dev/null || true)
if [ -n "$EXISTING_PIDS" ]; then
    echo -e "${YELLOW}Stopping previous API server (PIDs: $EXISTING_PIDS)...${NC}"
    echo "$EXISTING_PIDS" | xargs kill 2>/dev/null || true
    sleep 2
    # Force kill any survivors
    REMAINING=$(lsof -ti :39150 2>/dev/null || true)
    if [ -n "$REMAINING" ]; then
        echo "$REMAINING" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

# Register cleanup BEFORE starting the API server so there is no gap
# where a set -e failure or signal could leak a running process.
API_PID=""
CLEANING_UP=0
COMPOSE_FILES=()
cleanup() {
    [ "$CLEANING_UP" -eq 1 ] && return
    CLEANING_UP=1
    echo ""
    echo -e "${YELLOW}Shutting down snflwr.ai...${NC}"

    # Kill the API server and its children (uvicorn workers).
    # Note: kill -- -$PID (process-group kill) does NOT work in non-interactive
    # scripts because job control is off and background processes are not
    # process group leaders.  Use pkill -P to target children by parent PID.
    if [ -n "$API_PID" ]; then
        kill "$API_PID" 2>/dev/null || true
        # Give uvicorn 5 s to shut down workers gracefully
        for _i in 1 2 3 4 5; do
            kill -0 "$API_PID" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$API_PID" 2>/dev/null; then
            # Supervisor didn't exit — force-kill it and any remaining workers
            pkill -9 -P "$API_PID" 2>/dev/null || true
            kill -9 "$API_PID" 2>/dev/null || true
        fi
    fi

    # Stop Ollama only if this script started it
    if [ -n "${OLLAMA_PID:-}" ]; then
        kill "$OLLAMA_PID" 2>/dev/null || true
    fi

    if [ -n "${COMPOSE_CMD:-}" ] && [ -f "${COMPOSE_FILE:-}" ]; then
        if [ ${#COMPOSE_FILES[@]} -gt 0 ] 2>/dev/null; then
            $COMPOSE_CMD "${COMPOSE_FILES[@]}" down 2>/dev/null || true
        else
            $COMPOSE_CMD -f "$COMPOSE_FILE" down 2>/dev/null || true
        fi
    fi
    echo -e "${GREEN}snflwr.ai stopped.${NC}"
}
trap cleanup SIGINT SIGTERM EXIT

# Start snflwr.ai API server in background
echo -e "${GREEN}Starting snflwr.ai API server...${NC}"
python -m api.server > logs/api.log 2>&1 &
API_PID=$!
echo "API server PID: $API_PID"

# Wait for API to start
echo "Waiting for API to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:39150/health >/dev/null 2>&1; then
        echo -e "${GREEN}[OK] API server is running${NC}"
        break
    fi
    sleep 1
done

if ! curl -s http://localhost:39150/health >/dev/null 2>&1; then
    echo -e "${RED}ERROR: API server failed to start${NC}"
    echo "Check logs/api.log for details"
    kill $API_PID 2>/dev/null || true
    exit 1
fi

# Check if the Docker permission error is a group membership issue
check_docker_permission() {
    local docker_err
    docker_err=$(docker info 2>&1)
    if echo "$docker_err" | grep -qi "permission denied"; then
        return 0  # yes, it's a permission problem
    fi
    return 1  # some other issue
}

# Ensure Docker daemon is reachable
ensure_docker_running() {
    # Quick check: can we talk to Docker?
    if docker info >/dev/null 2>&1; then
        return 0
    fi

    # macOS: try launching Docker Desktop
    if [[ "$(uname)" == "Darwin" ]]; then
        if [ -d "/Applications/Docker.app" ]; then
            echo -e "${YELLOW}Starting Docker Desktop...${NC}"
            open -a Docker 2>/dev/null || true
            for i in {1..30}; do
                sleep 2
                if docker info >/dev/null 2>&1; then
                    return 0
                fi
            done
        fi
        return 1
    fi

    # Linux: Docker Desktop context may be active but Desktop isn't running.
    # If the system daemon socket exists, switch to the default context.
    if [ -S /var/run/docker.sock ]; then
        local active_context
        active_context=$(docker context show 2>/dev/null || echo "")
        if [ "$active_context" != "default" ]; then
            echo -e "${YELLOW}Docker Desktop not running — switching to system Docker daemon${NC}"
            docker context use default >/dev/null 2>&1 || true
            if docker info >/dev/null 2>&1; then
                return 0
            fi
        fi
    fi

    # Check if the daemon is running but we lack permission
    if check_docker_permission; then
        # Daemon is running, user just can't connect — return special code
        return 2
    fi

    # Try starting the system Docker service (without sudo first)
    if command -v systemctl &>/dev/null; then
        # Try without sudo — works if user has permissions
        systemctl start docker 2>/dev/null || true
        sleep 2
        if docker info >/dev/null 2>&1; then
            return 0
        fi

        # Only try sudo if it won't prompt for a password
        if sudo -n true 2>/dev/null; then
            echo -e "${YELLOW}Starting Docker daemon...${NC}"
            sudo systemctl start docker 2>/dev/null || true
            sleep 2
            if docker info >/dev/null 2>&1; then
                return 0
            fi
        fi
    fi

    # After starting, check again if it's a permission issue
    if check_docker_permission; then
        return 2
    fi

    return 1
}

# Docker is required — Open WebUI is the user-facing chat interface
COMPOSE_CMD=$(detect_compose_cmd)

if [ -z "$COMPOSE_CMD" ]; then
    echo -e "${RED}ERROR: Docker with Compose is required but not found${NC}"
    echo "snflwr.ai uses Open WebUI as its chat interface, which requires Docker."
    echo ""
    echo "Install Docker:"
    echo "  Linux:  curl -fsSL https://get.docker.com | sh"
    echo "  macOS:  brew install --cask docker"
    echo "  Or visit: https://docs.docker.com/get-docker/"
    echo ""
    echo "After installing Docker, re-run:  ./start_snflwr.sh"
    exit 1
fi

docker_rc=0
ensure_docker_running || docker_rc=$?
if [ $docker_rc -eq 2 ]; then
    echo -e "${RED}ERROR: Docker is running but your user lacks permission${NC}"
    echo ""
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "Try restarting Docker Desktop, or reinstall it from:"
        echo "  https://www.docker.com/products/docker-desktop/"
    else
        echo "Fix it with (requires logout/login afterward):"
        echo ""
        echo "  sudo usermod -aG docker $USER"
        echo ""
        echo "Then log out and back in, and re-run:"
    fi
    echo "  ./start_snflwr.sh"
    exit 1
elif [ $docker_rc -ne 0 ]; then
    echo -e "${RED}ERROR: Docker daemon is not running${NC}"
    echo ""
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "Start Docker Desktop, then re-run this script:"
        echo ""
        echo "  open -a Docker"
        echo "  ./start_snflwr.sh"
    else
        echo "Start it manually, then re-run this script:"
        echo ""
        echo "  sudo systemctl start docker"
        echo "  ./start_snflwr.sh"
        echo ""
        echo "To avoid this in the future, enable Docker at boot:"
        echo "  sudo systemctl enable docker"
    fi
    exit 1
fi

echo -e "${GREEN}[OK] Docker is running${NC}"

# Start Open WebUI via Docker Compose
COMPOSE_FILE="$SCRIPT_DIR/frontend/open-webui/docker-compose.yaml"
COMPOSE_HOSTNET="$SCRIPT_DIR/frontend/open-webui/docker-compose.hostnet.yaml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo -e "${RED}ERROR: Open WebUI compose file not found at $COMPOSE_FILE${NC}"
    echo "Re-run the installer:  python3 install.py"
    exit 1
fi

# On Linux, use host networking to avoid firewall issues with Docker bridge.
# On macOS, use bridge networking with host.docker.internal (host mode doesn't work).
COMPOSE_FILES=(--env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE")
if [[ "$(uname)" == "Linux" ]] && [ -f "$COMPOSE_HOSTNET" ]; then
    COMPOSE_FILES+=(-f "$COMPOSE_HOSTNET")
    echo -e "${YELLOW}Using host networking (Linux)${NC}"
fi

echo ""

# Determine the expected image tag
WEBUI_TAG="${WEBUI_DOCKER_TAG:-v0.8.3}"
WEBUI_IMAGE="ghcr.io/open-webui/open-webui:${WEBUI_TAG}"

# Only pull if the image isn't cached locally
if docker image inspect "$WEBUI_IMAGE" >/dev/null 2>&1; then
    echo -e "${GREEN}Open WebUI image already cached locally, skipping pull.${NC}"
else
    echo -e "${GREEN}Pulling Open WebUI Docker image (this may take a few minutes on first run)...${NC}"
    PULL_OK=false
    for attempt in 1 2 3; do
        if $COMPOSE_CMD "${COMPOSE_FILES[@]}" pull 2>&1; then
            PULL_OK=true
            break
        fi
        if [ "$attempt" -lt 3 ]; then
            wait_secs=$((attempt * 2))
            echo -e "${YELLOW}  Pull attempt $attempt failed, retrying in ${wait_secs}s...${NC}"
            sleep "$wait_secs"
        fi
    done
    if [ "$PULL_OK" = false ]; then
        echo -e "${YELLOW}WARNING: Failed to pull Open WebUI image after 3 attempts${NC}"
        echo "Will try to start with cached image (if available)..."
    fi
fi

# Kill anything already occupying port 3000 (stale container, leftover process, etc.)
# Also stop a leftover open-webui container from a previous run
docker rm -f open-webui 2>/dev/null || true

PORT3000_PIDS=""
if command -v lsof &>/dev/null; then
    PORT3000_PIDS=$(lsof -ti :3000 2>/dev/null || true)
elif command -v ss &>/dev/null; then
    PORT3000_PIDS=$(ss -tlnp sport = :3000 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
elif command -v fuser &>/dev/null; then
    PORT3000_PIDS=$(fuser 3000/tcp 2>/dev/null || true)
fi
if [ -n "$PORT3000_PIDS" ]; then
    echo -e "${YELLOW}Port 3000 is already in use — stopping the occupying process...${NC}"
    echo "$PORT3000_PIDS" | xargs kill 2>/dev/null || true
    sleep 2
    # Force-kill any survivors
    REMAINING3000=""
    if command -v lsof &>/dev/null; then
        REMAINING3000=$(lsof -ti :3000 2>/dev/null || true)
    elif command -v fuser &>/dev/null; then
        REMAINING3000=$(fuser 3000/tcp 2>/dev/null || true)
    fi
    if [ -n "$REMAINING3000" ]; then
        echo "$REMAINING3000" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

# Always attempt to start — a cached image from a previous run may still work
WEBUI_RUNNING=false
echo -e "${GREEN}Starting Open WebUI frontend...${NC}"

# Try up to 2 attempts (handles transient Docker errors)
COMPOSE_OK=false
for attempt in 1 2; do
    if $COMPOSE_CMD "${COMPOSE_FILES[@]}" up -d 2>&1; then
        COMPOSE_OK=true
        break
    fi
    if [ "$attempt" -eq 1 ]; then
        echo -e "${YELLOW}  Compose attempt 1 failed, retrying...${NC}"
        sleep 3
    fi
done

if [ "$COMPOSE_OK" = true ]; then
    # Wait for Open WebUI to be ready (up to ~120 s on first run)
    echo "Waiting for Open WebUI to be ready..."
    WEBUI_READY=false
    for i in {1..60}; do
        if curl -s http://localhost:3000 >/dev/null 2>&1; then
            echo -e "${GREEN}[OK] Open WebUI is running${NC}"
            WEBUI_READY=true
            break
        fi
        # Check if container exited unexpectedly
        if [ "$i" -eq 10 ] || [ "$i" -eq 30 ]; then
            CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' open-webui 2>/dev/null || echo "unknown")
            if [ "$CONTAINER_STATUS" = "exited" ] || [ "$CONTAINER_STATUS" = "dead" ]; then
                echo -e "${YELLOW}Container exited — restarting...${NC}"
                $COMPOSE_CMD "${COMPOSE_FILES[@]}" up -d 2>&1 || true
            fi
        fi
        sleep 2
    done
    if [ "$WEBUI_READY" = false ]; then
        echo -e "${YELLOW}Open WebUI is starting up (may take a minute on first run)${NC}"
    fi
    # Container started (even if still warming up)
    WEBUI_RUNNING=true
else
    echo -e "${YELLOW}WARNING: Failed to start Open WebUI via Docker${NC}"
    echo "Try running manually: $COMPOSE_CMD ${COMPOSE_FILES[*]} up -d"
    echo "Continuing with API server only."
fi

echo ""
echo "=========================================="
echo -e "${GREEN}[OK] snflwr.ai is running!${NC}"
echo "=========================================="
echo ""
echo -e "  Chat UI:          ${GREEN}http://localhost:3000${NC}"
echo -e "  Admin dashboard:  ${GREEN}http://localhost:39150/admin${NC}"
echo ""
echo "To stop:"
echo "  - Press Ctrl+C"
echo ""
echo "Logs: $SCRIPT_DIR/logs/api.log"
echo ""

if [ "$HEADLESS" = true ]; then
    # GUI launcher manages the lifecycle and has its own "Open in Browser"
    # button — don't auto-open a tab the launcher can't close on stop.
    echo "Running in headless mode. PID: $$"
    # "|| true" prevents set -e from exiting before the EXIT trap can
    # run cleanup when wait is interrupted by SIGINT (Ctrl+C).
    wait $API_PID || true
else
    # Interactive (terminal) mode — open browser and tail logs
    if [ "$WEBUI_RUNNING" = true ]; then
        open_browser "http://localhost:3000"
    else
        open_browser "http://localhost:39150/admin"
    fi
    tail -f logs/api.log || true
fi
