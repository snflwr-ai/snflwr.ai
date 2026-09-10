#!/bin/bash
# snflwr.ai - Enterprise Production Build Script
# Builds all Docker images with models baked in
#
# Enterprise builds ALWAYS include the LLM safety classifier (Llama Guard).
# This is mandatory for K-12 school deployments — cannot be opted out.
#
# The script accounts for combined RAM usage of chat + safety models plus
# services overhead (PostgreSQL, Redis, nginx, API, Celery, OS).
#
# The user-facing chat model is always 'snflwr.ai' — built locally by
# docker/Dockerfile.ollama as a wrapper around the BASE model selected
# here. Kids never see the raw base-model tag in the chat dropdown.
#
# Usage:
#   enterprise/build.sh                                                  # interactive
#   enterprise/build.sh --model gemma4:e4b                               # specify base model
#   enterprise/build.sh --model gemma4:e4b --safety llama-guard3:8b      # specify both
#   enterprise/build.sh --auto                                           # auto-select by RAM

set -e

# ── Helpers ──────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[FAIL]${NC} $1"; }
heading() { echo -e "\n${BOLD}$1${NC}"; }

# ── Parse arguments ──────────────────────────────────────────────────────────

CHAT_MODEL=""
SAFETY_MODEL=""
AUTO_SELECT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --model)   CHAT_MODEL="$2"; shift 2 ;;
        --safety)  SAFETY_MODEL="$2"; shift 2 ;;
        --auto)    AUTO_SELECT=true; shift ;;
        -h|--help)
            echo "Usage: enterprise/build.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --model MODEL     Base model used to build snflwr.ai (e.g., gemma4:e4b)"
            echo "  --safety MODEL    Safety classifier model (e.g., llama-guard3:8b)"
            echo "  --auto            Auto-select models based on server RAM"
            echo "  -h, --help        Show this help"
            echo ""
            echo "The user-facing chat model is always 'snflwr.ai' — built locally"
            echo "by docker/Dockerfile.ollama as a wrapper around the base below."
            echo ""
            echo "Base model tiers — gemma4:e4b is the default backbone; small"
            echo "boxes below the minimum are UNSUPPORTED (no safe smaller backbone):"
            echo "  gemma4:e4b    ~10 GB runtime  Default — recommended backbone (16 GB+)"
            echo "  (below ~12 GB usable: unsupported — build will refuse)"
            echo ""
            echo "Safety classifier tiers (Meta Llama Guard):"
            echo "  llama-guard3:1b   ~2 GB runtime   Fast, good accuracy"
            echo "  llama-guard3:8b   ~5 GB runtime   Higher accuracy"
            echo ""
            echo "RAM budget: models + ~4 GB for services (PostgreSQL, Redis, etc.)"
            echo "Enterprise builds always enable the safety classifier."
            exit 0
            ;;
        *)  error "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Banner ───────────────────────────────────────────────────────────────────

echo "======================================"
echo "  snflwr.ai - Enterprise Build"
echo "======================================"
echo ""
echo "  Safety classifier: ENABLED (mandatory for enterprise)"
echo ""

# ── Prerequisites ────────────────────────────────────────────────────────────

heading "Checking prerequisites..."

command -v docker >/dev/null 2>&1 || { error "Docker not found. Please install Docker."; exit 1; }
info "Docker found: $(docker --version 2>&1 | head -1)"

# Accept either `docker compose` (v2 plugin) or `docker-compose` (standalone)
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    error "Docker Compose not found. Please install Docker Compose."
    exit 1
fi
info "Docker Compose found: $($COMPOSE version 2>&1 | head -1)"

# Detect NVIDIA GPU
USE_GPU=false
VRAM_GB=0
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
    if docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi &>/dev/null 2>&1; then
        USE_GPU=true
        # Capture VRAM, not just the name. Until 2026-09-10 this block detected a
        # GPU purely to print it and to set --gpus, then sized the models on RAM
        # anyway — so a GPU server was judged as if it were CPU-only and could be
        # told it was unsupported while a perfectly capable card sat idle.
        VRAM_GB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
                  | head -1 | awk '{printf "%d", $1/1024}')
        VRAM_GB=${VRAM_GB:-0}
        info "GPU detected: ${GPU_NAME} (${VRAM_GB} GB VRAM, nvidia-container-toolkit confirmed)"
    else
        warn "GPU detected (${GPU_NAME}) but nvidia-container-toolkit not configured — using CPU."
        warn "To enable: sudo apt install nvidia-container-toolkit && sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    fi
else
    warn "No NVIDIA GPU detected — Ollama will run CPU inference."
fi

# Check for .env file
if [ ! -f .env.production ]; then
    error ".env.production not found!"
    echo "   Run: python scripts/setup_production.py"
    exit 1
fi
info "Environment configuration found (.env.production)"

# ── RAM detection and model budget ───────────────────────────────────────────

# Approximate runtime RAM per model (GB). These are estimates for the model
# loaded in memory — actual usage varies by context length and batch size.
SERVICES_OVERHEAD=4   # PostgreSQL, Redis, nginx, API server, Celery, OS

chat_model_ram() {
    case $1 in
        gemma4:e4b)   echo 10 ;;
        *)           echo 10 ;;
    esac
}

safety_model_ram() {
    case $1 in
        llama-guard3:1b) echo 2 ;;
        llama-guard3:8b) echo 5 ;;
        *)               echo 2 ;;
    esac
}

detect_ram_gb() {
    local ram_kb
    if [ -f /proc/meminfo ]; then
        ram_kb=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)
        # Round to nearest GB (add half a GB in kB before truncating)
        echo $(( (ram_kb + 524288) / 1024 / 1024 ))
    elif command -v sysctl >/dev/null 2>&1; then
        # macOS
        local ram_bytes
        ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
        echo $(( (ram_bytes + 536870912) / 1024 / 1024 / 1024 ))
    else
        echo 0
    fi
}

# Recommend a (chat, safety) pair that fits the hardware budget — VRAM when
# the tutor will live on a GPU, otherwise RAM.
# Strategy: maximize chat model quality, then use the best safety model
# that fits in the remaining budget.
recommend_models() {
    local ram_gb=$1
    local vram_gb=${2:-0}
    local budget=$(( ram_gb - SERVICES_OVERHEAD ))

    # When the tutor will actually live on a GPU, size on VRAM. gemma4:e4b is
    # 3.3 GB RESIDENT on a card (measured 2026-09-10) versus a 9.5 GB RAM
    # footprint, and the safety classifier is CPU-pinned by design so it costs
    # RAM, never VRAM. A 6 GB card therefore runs the BEST backbone; sizing that
    # same machine on RAM would have called it unsupported.
    if [ "${vram_gb:-0}" -ge 6 ]; then
        # Guard still needs RAM alongside; keep a floor so we do not hand a
        # GPU box an image whose classifier cannot stay resident.
        if [ "$ram_gb" -ge 8 ]; then
            local safety="llama-guard3:1b"
            [ "$ram_gb" -ge 13 ] && safety="llama-guard3:8b"
            echo "gemma4:e4b|${safety}"
            return 0
        fi
    fi

    # gemma4 is the ONLY backbone family. It won the 2026-06-17 tutoring bake-off
    # outright at a fraction of the VRAM of the larger dense tiers, and re-measured
    # 2026-09-10 over 3 repeats it remains the best quality-per-GB of the family.
    #
    # The small-box fallbacks were REMOVED 2026-09-10. They were a different model
    # family, so a small enterprise image shipped a DIFFERENT tutor that none of
    # the persona, pedagogy or S9051B compliance work had been measured against —
    # and gemma4:e2b, the one in-family candidate, measured 9 points below e4b
    # overall and 7 below on homework integrity. An image that cannot run a
    # measured-safe tutor must fail to build, not build a weaker one silently.
    local chat safety
    if   [ "$budget" -ge 15 ]; then chat="gemma4:e4b";   safety="llama-guard3:8b"   # 10+5=15
    elif [ "$budget" -ge 12 ]; then chat="gemma4:e4b";   safety="llama-guard3:1b"   # 10+2=12
    else
        echo "UNSUPPORTED|"
        return 0
    fi

    echo "${chat}|${safety}"
}

RAM_GB=$(detect_ram_gb)

# ── Auto or flag-based selection ─────────────────────────────────────────────

if [ "$AUTO_SELECT" = true ]; then
    if [ "$RAM_GB" -eq 0 ]; then
        warn "Could not detect RAM. Using gemma4:e4b + llama-guard3:1b (16 GB+ assumed)"
        CHAT_MODEL="${CHAT_MODEL:-gemma4:e4b}"
        SAFETY_MODEL="${SAFETY_MODEL:-llama-guard3:1b}"
    else
        PAIR=$(recommend_models "$RAM_GB" "$VRAM_GB")
        if [ "${PAIR%%|*}" = "UNSUPPORTED" ] && [ -z "$CHAT_MODEL" ]; then
            # No safe backbone fits. Fail the BUILD rather than bake a weaker
            # tutor into an enterprise image that then ships to classrooms.
            echo "ERROR: ${RAM_GB} GB RAM is below the minimum for a supported backbone" >&2
            echo "       (need ~15 GB for gemma4:e4b + llama-guard3:8b, or ~12 GB with the 1b guard)." >&2
            echo "       Set CHAT_MODEL=<tag> explicitly to override." >&2
            exit 1
        fi
        CHAT_MODEL="${CHAT_MODEL:-${PAIR%%|*}}"
        SAFETY_MODEL="${SAFETY_MODEL:-${PAIR##*|}}"
        info "Auto-selected for ${RAM_GB} GB RAM: ${CHAT_MODEL} + ${SAFETY_MODEL}"
    fi
fi

# ── Interactive chat model selection ─────────────────────────────────────────

if [ -z "$CHAT_MODEL" ]; then
    heading "Base Model Selection"
    echo ""
    echo "   The user-facing chat model is always 'snflwr.ai' — built as a wrapper"
    echo "   around the base model you choose below. Kids never see the raw"
    echo "   base-model tag in the Open WebUI dropdown."
    echo ""

    if [ "$RAM_GB" -gt 0 ]; then
        PAIR=$(recommend_models "$RAM_GB" "$VRAM_GB")
        REC_CHAT="${PAIR%%|*}"
        [ "$REC_CHAT" = "UNSUPPORTED" ] && REC_CHAT="(none — this server is below the minimum)"
        MODEL_BUDGET=$(( RAM_GB - SERVICES_OVERHEAD ))
        echo "   Detected server RAM:  ${RAM_GB} GB"
        echo "   Detected GPU VRAM:    ${VRAM_GB} GB"
        echo "   Services overhead:    ~${SERVICES_OVERHEAD} GB (PostgreSQL, Redis, nginx, API, OS)"
        if [ "${VRAM_GB:-0}" -ge 6 ]; then
            echo "   Sizing on:            VRAM (the tutor will live on the GPU; ~3.3 GB resident)"
        else
            echo "   Sizing on:            RAM (~${MODEL_BUDGET} GB for chat + safety combined)"
        fi
        echo "   Recommended base:     ${REC_CHAT}"
    else
        REC_CHAT="gemma4:e4b"
        echo "   Could not detect RAM. Default: ${REC_CHAT}"
    fi

    echo ""
    echo "   Available base models (gemma4 only — see note below):"
    echo "   ─────────────────────────────────────────────────────────"
    echo "    1) gemma4:e4b     ~10 GB runtime   Default — recommended (16 GB+)"
    echo "    2) gemma4:12b     ~8 GB runtime    Dense; only if e4b will not fit"
    echo "   ─────────────────────────────────────────────────────────"
    echo "   Smaller tiers were removed 2026-09-10. They were a different model"
    echo "   family (a DIFFERENT tutor, none of the compliance work measured on"
    echo "   it), and the one small in-family option measured 9 points worse"
    echo "   overall and 7 worse at withholding homework answers."
    echo ""

    read -rp "   Select base model [1-2] or Enter for ${REC_CHAT}: " choice

    case "${choice}" in
        1) CHAT_MODEL="gemma4:e4b" ;;
        2) CHAT_MODEL="gemma4:12b" ;;
        "") CHAT_MODEL="$REC_CHAT" ;;
        *)
            warn "Invalid choice '${choice}'. Using ${REC_CHAT}"
            CHAT_MODEL="$REC_CHAT"
            ;;
    esac

    info "Base model: ${CHAT_MODEL}  (snflwr.ai will be built on top of this)"
elif [ "$AUTO_SELECT" = false ]; then
    info "Base model: ${CHAT_MODEL} (from --model flag; snflwr.ai built on top)"
fi

# ── Interactive safety model selection ───────────────────────────────────────

if [ -z "$SAFETY_MODEL" ]; then
    heading "Safety Classifier Selection"
    echo ""
    echo "   The LLM safety classifier runs on every message to detect unsafe"
    echo "   content that pattern matching alone might miss. This is mandatory"
    echo "   for enterprise K-12 deployments."
    echo ""

    CHAT_RAM=$(chat_model_ram "$CHAT_MODEL")

    if [ "$RAM_GB" -gt 0 ]; then
        REMAINING=$(( RAM_GB - SERVICES_OVERHEAD - CHAT_RAM ))
        echo "   Server RAM:          ${RAM_GB} GB"
        echo "   Services overhead:   ~${SERVICES_OVERHEAD} GB"
        echo "   Chat model (${CHAT_MODEL}): ~${CHAT_RAM} GB"
        echo "   Remaining for safety: ~${REMAINING} GB"
        echo ""

        if [ "$REMAINING" -ge 5 ]; then
            REC_SAFETY="llama-guard3:8b"
        else
            REC_SAFETY="llama-guard3:1b"
        fi

        if [ "$REMAINING" -lt 2 ]; then
            warn "Very tight RAM budget. Consider a smaller chat model or more RAM."
            REC_SAFETY="llama-guard3:1b"
        fi
    else
        REC_SAFETY="llama-guard3:1b"
        echo "   Could not detect RAM. Default: ${REC_SAFETY}"
        echo ""
    fi

    echo "   Available safety models (Meta Llama Guard):"
    echo "   ─────────────────────────────────────────────────────────"
    echo "    1) llama-guard3:1b    ~2 GB runtime   Fast, good accuracy"
    echo "    2) llama-guard3:8b    ~5 GB runtime   Higher accuracy"
    echo "   ─────────────────────────────────────────────────────────"
    echo ""

    read -rp "   Select safety model [1-2] or Enter for ${REC_SAFETY}: " safety_choice

    case "${safety_choice}" in
        1) SAFETY_MODEL="llama-guard3:1b" ;;
        2) SAFETY_MODEL="llama-guard3:8b" ;;
        "") SAFETY_MODEL="$REC_SAFETY" ;;
        *)
            warn "Invalid choice '${safety_choice}'. Using ${REC_SAFETY}"
            SAFETY_MODEL="$REC_SAFETY"
            ;;
    esac

    info "Safety model: ${SAFETY_MODEL}"
elif [ "$AUTO_SELECT" = false ]; then
    info "Safety model: ${SAFETY_MODEL} (from --safety flag)"
fi

# ── Validate combined RAM budget ─────────────────────────────────────────────

CHAT_RAM=$(chat_model_ram "$CHAT_MODEL")
SAFETY_RAM=$(safety_model_ram "$SAFETY_MODEL")
TOTAL_MODEL_RAM=$(( CHAT_RAM + SAFETY_RAM ))
TOTAL_REQUIRED=$(( TOTAL_MODEL_RAM + SERVICES_OVERHEAD ))

echo ""
heading "RAM Budget"
echo "   Chat model (${CHAT_MODEL}):    ~${CHAT_RAM} GB"
echo "   Safety model (${SAFETY_MODEL}): ~${SAFETY_RAM} GB"
echo "   Services overhead:           ~${SERVICES_OVERHEAD} GB"
echo "   ─────────────────────────────────────────"
echo "   Total estimated:             ~${TOTAL_REQUIRED} GB"

if [ "$RAM_GB" -gt 0 ]; then
    echo "   Server RAM:                   ${RAM_GB} GB"

    if [ "$TOTAL_REQUIRED" -gt "$RAM_GB" ]; then
        echo ""
        warn "Selected models require ~${TOTAL_REQUIRED} GB but server has ${RAM_GB} GB RAM."
        warn "The system may swap heavily or OOM. Consider:"
        warn "  - A smaller chat model (e.g., one tier down)"
        warn "  - llama-guard3:1b instead of 8b for the safety model"
        warn "  - Adding more RAM to the server"
        echo ""
        read -rp "   Continue anyway? [y/N]: " confirm
        if [[ ! "$confirm" =~ ^[Yy] ]]; then
            echo "   Aborted. Re-run with different model selections."
            exit 1
        fi
    elif [ $(( TOTAL_REQUIRED + 2 )) -gt "$RAM_GB" ]; then
        warn "Tight fit (~${TOTAL_REQUIRED} GB needed, ${RAM_GB} GB available). Monitor memory usage after deployment."
    else
        info "RAM budget OK (~${TOTAL_REQUIRED} GB needed, ${RAM_GB} GB available)"
    fi
fi

echo ""

# ── Build Ollama image ───────────────────────────────────────────────────────

heading "Step 1/3: Building Ollama image with models..."
echo "   Chat model:   ${CHAT_MODEL} (~${CHAT_RAM} GB)"
echo "   Safety model:  ${SAFETY_MODEL} (~${SAFETY_RAM} GB)"
echo "   + student tutor: snflwr-ai (persona on ${CHAT_MODEL})"
echo "   This may take 10-20 minutes on the first build..."
echo ""

docker build \
    -f docker/Dockerfile.ollama \
    --build-arg CHAT_MODEL="${CHAT_MODEL}" \
    --build-arg SAFETY_MODEL="${SAFETY_MODEL}" \
    -t snflwr-ollama:latest \
    .

info "Ollama image built with ${CHAT_MODEL} + ${SAFETY_MODEL}"

# ── Build API image ──────────────────────────────────────────────────────────

heading "Step 2/3: Building Snflwr API..."

docker build -f docker/Dockerfile -t snflwr-api:latest .

info "Snflwr API image built"

# ── Pull supporting images ───────────────────────────────────────────────────

heading "Step 3/3: Pulling supporting images..."

$COMPOSE -f docker/compose/docker-compose.yml pull nginx postgres redis

info "Supporting images pulled"

# ── Ensure ENABLE_SAFETY_MODEL=true in .env.production ───────────────────────

if grep -q "^ENABLE_SAFETY_MODEL=" .env.production; then
    if grep -q "^ENABLE_SAFETY_MODEL=true" .env.production; then
        info "Safety model enabled in .env.production"
    else
        warn "Setting ENABLE_SAFETY_MODEL=true in .env.production (required for enterprise)"
        sed -i.bak 's/^ENABLE_SAFETY_MODEL=.*/ENABLE_SAFETY_MODEL=true/' .env.production && rm -f .env.production.bak
        info "Safety model enabled in .env.production"
    fi
else
    echo "ENABLE_SAFETY_MODEL=true" >> .env.production
    info "Added ENABLE_SAFETY_MODEL=true to .env.production"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

heading "Built Images:"
echo "   ─────────────────────────────────────────────────────────"
docker images --format "   {{.Repository}}:{{.Tag}}\t{{.Size}}" | grep -E "snflwr"
echo "   ─────────────────────────────────────────────────────────"
echo ""

echo "======================================"
echo -e "  ${GREEN}[OK] Build Complete!${NC}"
echo "======================================"
echo ""
echo "  Chat model:    ${CHAT_MODEL} (~${CHAT_RAM} GB runtime)"
echo "  Safety model:  ${SAFETY_MODEL} (~${SAFETY_RAM} GB runtime)"
echo "  Combined:      ~${TOTAL_REQUIRED} GB (models + services)"
echo "  Safety filter:  ENABLED (enterprise mandatory)"
echo ""
if [ "$USE_GPU" = true ]; then
    START_CMD="$COMPOSE -f docker/compose/docker-compose.yml -f docker/compose/docker-compose.gpu.yml up -d"
else
    START_CMD="$COMPOSE -f docker/compose/docker-compose.yml up -d"
fi

echo "  GPU acceleration: $([ "$USE_GPU" = true ] && echo "ENABLED" || echo "CPU only")"
echo ""
echo "  Next steps:"
echo "  1. Review .env.production"
echo "  2. Set up SSL: enterprise/nginx/ssl/"
echo "  3. Start:  ${START_CMD}"
echo ""
echo "  Full guide: enterprise/README.md"
echo ""
