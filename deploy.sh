#!/usr/bin/env bash
# =============================================================================
# snflwr.ai - Home Server Deployment Script
# =============================================================================
#
# One-command Docker deployment for home servers, VPS, and self-hosters.
# Automatically detects GPU, generates secrets, and gets snflwr.ai running.
#
# Usage:
#   ./deploy.sh                  # auto-detect GPU, open browser when ready
#   ./deploy.sh --no-browser     # headless (servers without display)
#   ./deploy.sh --gpu            # force GPU mode (NVIDIA)
#   ./deploy.sh --no-gpu         # force CPU mode
#   ./deploy.sh --model <name>   # override AI model (e.g. qwen3.5:4b)
#   ./deploy.sh --port <port>    # override web UI port (default: 3000)
#   ./deploy.sh --stop           # stop all services
#   ./deploy.sh --update         # pull latest images and restart
#   ./deploy.sh --logs           # tail service logs
#   ./deploy.sh --status         # show service health
#
# For USB/family use:           ./start_snflwr.sh
# For school/enterprise use:   enterprise/build.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".env.home"
COMPOSE_BASE="docker/compose/docker-compose.home.yml"
COMPOSE_GPU="docker/compose/docker-compose.gpu.yml"
API_HEALTH_URL="http://localhost:39150/health"

# --- Colour helpers ----------------------------------------------------------
_bold()    { printf '\033[1m%s\033[0m' "$*"; }
_green()   { printf '\033[32m%s\033[0m' "$*"; }
_yellow()  { printf '\033[33m%s\033[0m' "$*"; }
_red()     { printf '\033[31m%s\033[0m' "$*"; }
_cyan()    { printf '\033[36m%s\033[0m' "$*"; }

banner() {
    echo ""
    echo "  $(_bold "$(_cyan 'snflwr.ai')")  |  Home Server Deployment"
    echo "  K-12 Safe AI Learning Platform"
    echo ""
}

info()    { echo "  $(_green '[+]') $*"; }
warn()    { echo "  $(_yellow '[!]') $*"; }
error()   { echo "  $(_red '[x]') $*" >&2; }
section() { echo ""; echo "  $(_bold "$*")"; echo "  $(printf '%0.s-' $(seq 1 ${#1}))"; }

# --- Port availability check -------------------------------------------------
# check_port PORT LABEL
#   Exits with a clear error if PORT is already bound on the host AND the
#   process holding it is NOT one of our own Docker containers.
check_port() {
    local port="$1" label="$2"

    # 1. Check if one of our containers already owns the port (previous run)
    local container_ids
    container_ids=$(docker ps -q --filter "publish=${port}" 2>/dev/null || true)
    if [[ -n "$container_ids" ]]; then
        local all_ours=true
        local foreign_name=""
        while IFS= read -r cid; do
            local cname
            cname=$(docker inspect --format '{{.Name}}' "$cid" 2>/dev/null | sed 's|^/||')
            if [[ "$cname" != snflwr-* ]]; then
                all_ours=false
                foreign_name="$cname"
                break
            fi
        done <<< "$container_ids"
        if [[ "$all_ours" == "true" ]]; then
            # Our own container(s) — compose will handle restart/replace
            return 0
        fi
        error "Port ${port} (${label}) is already in use by Docker container '${foreign_name}'."
        echo "      Stop it first:  docker stop ${foreign_name}"
        exit 1
    fi

    # 2. Check for non-Docker processes occupying the port
    local pids=""
    if command -v lsof &>/dev/null; then
        pids=$(lsof -ti :"${port}" 2>/dev/null || true)
    elif command -v ss &>/dev/null; then
        pids=$(ss -tlnp sport = :"${port}" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
    elif command -v fuser &>/dev/null; then
        pids=$(fuser "${port}/tcp" 2>/dev/null || true)
    fi

    if [[ -n "$pids" ]]; then
        # Filter out docker-proxy — it's Docker's own port forwarder and is
        # already covered by the container check above.
        local non_docker_pids=""
        local proc_info=""
        while IFS= read -r pid; do
            [[ -z "$pid" ]] && continue
            local comm
            comm=$(ps -p "$pid" -o comm= 2>/dev/null || true)
            if [[ "$comm" != "docker-proxy" ]]; then
                non_docker_pids="${non_docker_pids:+${non_docker_pids} }${pid}"
                proc_info="$comm"
            fi
        done <<< "$pids"

        if [[ -n "$non_docker_pids" ]]; then
            error "Port ${port} (${label}) is already in use${proc_info:+ by '${proc_info}'}."
            echo "      PID(s): $non_docker_pids"
            echo "      Free the port and re-run, or use --port to pick a different one."
            exit 1
        fi
    fi
}

# --- Argument parsing --------------------------------------------------------
GPU_MODE=""        # auto | force | none
OPEN_BROWSER=auto  # auto | yes | no
OLLAMA_MODEL_ARG=""
WEBUI_PORT_ARG=""
ACTION="start"     # start | stop | update | logs | status

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gpu)        GPU_MODE=force ;;
        --no-gpu)     GPU_MODE=none ;;
        --no-browser) OPEN_BROWSER=no ;;
        --model)      shift; OLLAMA_MODEL_ARG="$1" ;;
        --port)       shift; WEBUI_PORT_ARG="$1" ;;
        --stop)       ACTION=stop ;;
        --update)     ACTION=update ;;
        --logs)       ACTION=logs ;;
        --status)     ACTION=status ;;
        -h|--help)
            head -22 "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) error "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

# --- Helper: build compose command ------------------------------------------
compose_cmd() {
    # Returns the docker compose command with correct -f flags
    local use_gpu="${1:-false}"
    local cmd="docker compose -f $COMPOSE_BASE"
    if [[ "$use_gpu" == "true" ]]; then
        cmd="$cmd -f $COMPOSE_GPU"
    fi
    if [[ -f "$ENV_FILE" ]]; then
        cmd="$cmd --env-file $ENV_FILE"
    fi
    echo "$cmd"
}

# --- Stop action -------------------------------------------------------------
if [[ "$ACTION" == "stop" ]]; then
    banner
    section "Stopping snflwr.ai"
    # Try both GPU and non-GPU compose so we catch whichever was used
    docker compose -f "$COMPOSE_BASE" -f "$COMPOSE_GPU" \
        ${ENV_FILE:+--env-file "$ENV_FILE"} down 2>/dev/null \
        || docker compose -f "$COMPOSE_BASE" \
           ${ENV_FILE:+--env-file "$ENV_FILE"} down 2>/dev/null \
        || true
    info "Services stopped."
    exit 0
fi

# --- Logs action -------------------------------------------------------------
if [[ "$ACTION" == "logs" ]]; then
    docker compose -f "$COMPOSE_BASE" ${ENV_FILE:+--env-file "$ENV_FILE"} logs -f
    exit 0
fi

# --- Status action -----------------------------------------------------------
if [[ "$ACTION" == "status" ]]; then
    docker compose -f "$COMPOSE_BASE" ${ENV_FILE:+--env-file "$ENV_FILE"} ps
    exit 0
fi

# =============================================================================
# START / UPDATE
# =============================================================================

banner

# --- Check Docker ------------------------------------------------------------
section "Checking prerequisites"

if ! command -v docker &>/dev/null; then
    error "Docker is not installed."
    echo "      Install Docker Desktop: https://www.docker.com/get-started"
    echo "      Or on Linux:  curl -fsSL https://get.docker.com | sh"
    exit 1
fi

if ! docker info &>/dev/null; then
    error "Docker daemon is not running."
    echo "      Start Docker Desktop, or run:  sudo systemctl start docker"
    exit 1
fi

info "Docker $(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1) is running."

# --- Detect GPU --------------------------------------------------------------
USE_GPU=false
if [[ "$GPU_MODE" == "force" ]]; then
    USE_GPU=true
    info "GPU mode: forced ON."
elif [[ "$GPU_MODE" == "none" ]]; then
    USE_GPU=false
    info "GPU mode: forced OFF (CPU only)."
else
    # Auto-detect NVIDIA GPU
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
        USE_GPU=true
        info "GPU detected: $GPU_NAME -- enabling GPU acceleration."
    else
        warn "No NVIDIA GPU detected -- using CPU inference."
        warn "For GPU support: install nvidia-container-toolkit, then run with --gpu"
    fi
fi

# Verify NVIDIA Container Toolkit if GPU mode enabled
if [[ "$USE_GPU" == "true" ]]; then
    if ! docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 \
            nvidia-smi &>/dev/null 2>&1; then
        warn "GPU mode requested but NVIDIA Container Toolkit may not be configured."
        warn "Install: sudo apt install nvidia-container-toolkit"
        warn "Configure: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
        warn "Falling back to CPU mode."
        USE_GPU=false
    fi
fi

# --- Detect RAM and recommend model ------------------------------------------
section "Checking hardware"

TOTAL_RAM_GB=8
if [[ "$(uname)" == "Linux" ]]; then
    TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_RAM_GB=$(( TOTAL_RAM_KB / 1024 / 1024 ))
elif [[ "$(uname)" == "Darwin" ]]; then
    TOTAL_RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
fi

if   [[ $TOTAL_RAM_GB -ge 32 ]]; then RECOMMENDED_MODEL="qwen3.5:35b"
elif [[ $TOTAL_RAM_GB -ge 24 ]]; then RECOMMENDED_MODEL="qwen3.5:27b"
elif [[ $TOTAL_RAM_GB -ge 16 ]]; then RECOMMENDED_MODEL="qwen3.5:9b"
elif [[ $TOTAL_RAM_GB -ge  8 ]]; then RECOMMENDED_MODEL="qwen3.5:4b"
elif [[ $TOTAL_RAM_GB -ge  6 ]]; then RECOMMENDED_MODEL="qwen3.5:2b"
else                                   RECOMMENDED_MODEL="qwen3.5:0.8b"; fi

info "RAM: ${TOTAL_RAM_GB}GB -- recommended model: $RECOMMENDED_MODEL"

# --- Resolve final model -----------------------------------------------------
if [[ -n "$OLLAMA_MODEL_ARG" ]]; then
    RESOLVED_MODEL="$OLLAMA_MODEL_ARG"
    info "Model override: $RESOLVED_MODEL"
elif [[ -f "$ENV_FILE" ]] && grep -q "^OLLAMA_MODEL=" "$ENV_FILE"; then
    RESOLVED_MODEL=$(grep "^OLLAMA_MODEL=" "$ENV_FILE" | cut -d= -f2)
    info "Model from $ENV_FILE: $RESOLVED_MODEL"
else
    RESOLVED_MODEL="$RECOMMENDED_MODEL"
fi

# --- Resolve final port ------------------------------------------------------
RESOLVED_PORT="${WEBUI_PORT_ARG:-3000}"
if [[ -z "$WEBUI_PORT_ARG" ]] && [[ -f "$ENV_FILE" ]] && grep -q "^WEBUI_PORT=" "$ENV_FILE"; then
    RESOLVED_PORT=$(grep "^WEBUI_PORT=" "$ENV_FILE" | cut -d= -f2)
fi

# --- Generate .env.home if missing -------------------------------------------
section "Configuration"

_gen_secret() { python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null \
               || openssl rand -hex 32; }

if [[ ! -f "$ENV_FILE" ]]; then
    info "Generating $ENV_FILE with secure random secrets..."
    cat > "$ENV_FILE" <<EOF
# snflwr.ai Home Server Configuration
# Generated by deploy.sh on $(date)
# Keep this file private — it contains your security keys.

# AI Model (auto-selected based on RAM; override as needed)
OLLAMA_MODEL=${RESOLVED_MODEL}

# Web UI port (open http://localhost:${RESOLVED_PORT} when ready)
WEBUI_PORT=${RESOLVED_PORT}

# Security keys (auto-generated — do not share these)
JWT_SECRET_KEY=$(_gen_secret)
INTERNAL_API_KEY=$(_gen_secret)
WEBUI_SECRET_KEY=$(_gen_secret)
PARENT_DASHBOARD_PASSWORD=$(_gen_secret)

# Allow new user signups
# Must be 'true' for the very first run so you can create the admin account
# (Open WebUI promotes the first registered user to admin). Once your admin
# exists, set this to 'false' and re-run ./deploy.sh to lock down signups.
ENABLE_SIGNUP=true

# Log level (INFO, DEBUG, WARNING)
LOG_LEVEL=INFO

# Email alerts (optional — fill in to receive parent safety alerts)
SMTP_ENABLED=false
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USERNAME=you@gmail.com
# SMTP_PASSWORD=your-app-password
# SMTP_FROM_EMAIL=snflwr@yourdomain.com
# ADMIN_EMAIL=parent@yourdomain.com
EOF
    info "Created $ENV_FILE"
else
    # Update model/port if overridden via flags
    if [[ -n "$OLLAMA_MODEL_ARG" ]]; then
        sed -i "s|^OLLAMA_MODEL=.*|OLLAMA_MODEL=${RESOLVED_MODEL}|" "$ENV_FILE"
    fi
    if [[ -n "$WEBUI_PORT_ARG" ]]; then
        if grep -q "^WEBUI_PORT=" "$ENV_FILE"; then
            sed -i "s|^WEBUI_PORT=.*|WEBUI_PORT=${RESOLVED_PORT}|" "$ENV_FILE"
        else
            echo "WEBUI_PORT=${RESOLVED_PORT}" >> "$ENV_FILE"
        fi
    fi
    info "Using existing $ENV_FILE"
fi

# Export env vars for compose
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# --- Pre-flight port check ---------------------------------------------------
section "Checking ports"

check_port "$RESOLVED_PORT" "Web UI"
info "Port ${RESOLVED_PORT} (Web UI) is available."

# --- Build snflwr-api image --------------------------------------------------
section "Building images"

info "Building snflwr-api image (first build takes ~2 min)..."
docker build \
    -f docker/Dockerfile \
    -t snflwr-api:latest \
    . \
    --quiet \
    && info "snflwr-api image ready." \
    || { error "Image build failed. Run with DOCKER_BUILDKIT=1 for verbose output."; exit 1; }

# --- Start or update services ------------------------------------------------
section "Starting services"

CMD=$(compose_cmd "$USE_GPU")

if [[ "$ACTION" == "update" ]]; then
    info "Pulling latest images..."
    $CMD pull open-webui ollama 2>/dev/null || true
fi

info "Starting containers (daemon mode)..."
$CMD up -d --remove-orphans

# --- Wait for snflwr-api health ----------------------------------------------
section "Waiting for startup"

API_PORT="${API_PORT:-39150}"
WEBUI_PORT="${RESOLVED_PORT}"
MAX_WAIT=120
ELAPSED=0

printf "  Waiting for API"
while ! docker exec snflwr-api curl -sf http://localhost:39150/health &>/dev/null; do
    sleep 3
    ELAPSED=$(( ELAPSED + 3 ))
    printf "."
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
        echo ""
        error "API did not start within ${MAX_WAIT}s."
        error "Check logs: ./deploy.sh --logs"
        exit 1
    fi
done
echo ""
info "API is healthy."

printf "  Waiting for Open WebUI"
ELAPSED=0
while ! curl -sf "http://localhost:${WEBUI_PORT}" &>/dev/null; do
    sleep 3
    ELAPSED=$(( ELAPSED + 3 ))
    printf "."
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
        echo ""
        warn "Open WebUI not yet reachable at port ${WEBUI_PORT} — may still be starting."
        break
    fi
done
echo ""

# --- Pull model if not present -----------------------------------------------
section "AI model"

OLLAMA_MODEL="${OLLAMA_MODEL:-${RESOLVED_MODEL}}"

# Check if model is already available
if docker exec snflwr-ollama ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
    info "Model '$OLLAMA_MODEL' already downloaded."
else
    info "Downloading model '$OLLAMA_MODEL' (this may take several minutes on first run)..."
    docker exec snflwr-ollama ollama pull "$OLLAMA_MODEL" \
        && info "Model '$OLLAMA_MODEL' ready." \
        || warn "Model pull failed — the chat will try again when you first send a message."
fi

# --- Open browser ------------------------------------------------------------
APP_URL="http://localhost:${WEBUI_PORT}"

# Auto-detect headless: no DISPLAY and no WAYLAND_DISPLAY
if [[ "$OPEN_BROWSER" == "auto" ]]; then
    if [[ -z "${DISPLAY:-}" ]] && [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
        OPEN_BROWSER=no
    else
        OPEN_BROWSER=yes
    fi
fi

if [[ "$OPEN_BROWSER" == "yes" ]]; then
    if command -v xdg-open &>/dev/null; then
        xdg-open "$APP_URL" &>/dev/null &
    elif command -v open &>/dev/null; then
        open "$APP_URL" &>/dev/null &
    fi
fi

# --- Done --------------------------------------------------------------------
echo ""
echo "  ================================================================"
echo "  $(_bold "$(_green 'snflwr.ai is running!')")"
echo ""
echo "  Open: $(_bold "$(_cyan "$APP_URL")")"
echo ""
echo "  Useful commands:"
echo "    ./deploy.sh --status    # service health"
echo "    ./deploy.sh --logs      # live logs"
echo "    ./deploy.sh --update    # pull latest updates"
echo "    ./deploy.sh --stop      # stop everything"
echo "  ================================================================"
echo ""
