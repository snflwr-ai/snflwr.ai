#!/usr/bin/env bash
#
# Guarded upgrade framework for snflwr.ai application packages.
#
# One tool, many components. Each upgrade follows the same safe protocol:
#   resolve target -> pull -> SNAPSHOT rollback point -> apply -> SMOKE TEST
#   -> keep if green, else ROLL BACK to the previous state and re-verify.
# So a component upgrade can never leave the kids' app in a broken state.
#
# Usage:
#   scripts/guarded_upgrade.sh <component> [target] [--dry-run]
#
# Components:
#   owui     Open WebUI image            (target: image tag, default = latest stable)
#   ollama   Ollama inference engine     (target: image tag, default = latest stable)
#   model    Tutor model backbone        (target: REQUIRED base model tag, e.g. gemma4:e4b)
#
# Examples:
#   scripts/guarded_upgrade.sh owui                 # OWU -> latest stable, guarded
#   scripts/guarded_upgrade.sh ollama 0.30.10       # Ollama -> a specific tag
#   scripts/guarded_upgrade.sh model qwen3.5:8b     # rebuild tutor model on a new base
#   scripts/guarded_upgrade.sh owui --dry-run       # show target, change nothing
#
# Each component's smoke test verifies the integration most likely to break on
# that upgrade. None of them is a full safety evaluation: the proxy safety
# split is fail-closed (a lost user-role header degrades everyone to student =
# more filtering, never less), so the residual risk is functional, not safety.
# For a MODEL backbone change, still run `python -m evals.tutoring.run_eval`
# before trusting it in front of children.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.home"
COMPOSE_BASE="$REPO_DIR/docker/compose/docker-compose.home.yml"
COMPOSE_GPU="$REPO_DIR/docker/compose/docker-compose.gpu.yml"
SMOKE_TIMEOUT=180
DB_BACKUP="${TMPDIR:-/tmp}/snflwr-owui-webui.db.pre-upgrade.bak"
DB_IN_CONTAINER="/app/backend/data/webui.db"
MODEL_BACKUP_TAG="snflwr.ai:preupgrade-bak"

# --- pretty output -----------------------------------------------------------
if [[ -t 1 ]]; then
    _g() { printf '\033[32m%s\033[0m' "$1"; }
    _r() { printf '\033[31m%s\033[0m' "$1"; }
    _y() { printf '\033[33m%s\033[0m' "$1"; }
else
    _g() { printf '%s' "$1"; }; _r() { printf '%s' "$1"; }; _y() { printf '%s' "$1"; }
fi
info() { echo "  $(_g '[+]') $*"; }
warn() { echo "  $(_y '[!]') $*"; }
err()  { echo "  $(_r '[x]') $*" >&2; }
step() { echo ""; echo "  $1"; echo "  ${1//?/-}"; }

# --- shared helpers ----------------------------------------------------------
compose() {
    local files=(-f "$COMPOSE_BASE")
    [[ -f "$COMPOSE_GPU" ]] && files+=(-f "$COMPOSE_GPU")
    docker compose "${files[@]}" --env-file "$ENV_FILE" "$@"
}

get_env_var() {  # get_env_var VAR DEFAULT
    if [[ -f "$ENV_FILE" ]] && grep -q "^$1=" "$ENV_FILE"; then
        grep "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-
    else
        echo "$2"
    fi
}

set_env_var() {  # set_env_var VAR VALUE
    if grep -q "^$1=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
    else
        echo "$1=$2" >> "$ENV_FILE"
    fi
}

wait_healthy() {  # wait_healthy CONTAINER
    local elapsed=0
    printf "  waiting for %s health" "$1"
    while [[ "$(docker inspect "$1" --format '{{.State.Health.Status}}' 2>/dev/null)" != "healthy" ]]; do
        sleep 3; elapsed=$((elapsed + 3)); printf "."
        if [[ $elapsed -ge $SMOKE_TIMEOUT ]]; then
            echo ""; err "$1 did not become healthy within ${SMOKE_TIMEOUT}s"; return 1
        fi
    done
    echo ""; info "$1 is healthy"; return 0
}

# Run a python snippet inside snflwr-frontend with INTERNAL_API_KEY on stdin.
# $1 = python source (passed via -c so stdin stays free for the key).
run_with_key() {
    docker exec snflwr-api printenv INTERNAL_API_KEY 2>/dev/null \
        | docker exec -i snflwr-frontend python -c "$1"
}

# Authenticated proxy check shared by owui + ollama smoke tests: the proxy
# returns models for an authenticated caller AND rejects anonymous ones.
proxy_auth_ok() {
    run_with_key '
import sys, urllib.request, urllib.error, json
key = sys.stdin.read().strip()
base = "http://snflwr-api:39150/api/tags"
req = urllib.request.Request(base, headers={"Authorization": "Bearer " + key})
try:
    models = json.load(urllib.request.urlopen(req, timeout=15)).get("models", [])
except Exception as e:
    print("FAIL: authenticated /api/tags errored: %s" % e, file=sys.stderr); sys.exit(1)
if not models:
    print("FAIL: authenticated /api/tags returned no models", file=sys.stderr); sys.exit(1)
try:
    urllib.request.urlopen(base, timeout=15)
    print("FAIL: proxy accepted an UNAUTHENTICATED /api/tags", file=sys.stderr); sys.exit(1)
except urllib.error.HTTPError as e:
    if e.code != 401:
        print("FAIL: anonymous /api/tags returned %s, expected 401" % e.code, file=sys.stderr); sys.exit(1)
except Exception as e:
    print("FAIL: anonymous /api/tags errored: %s" % e, file=sys.stderr); sys.exit(1)
print("OK: %d model(s) via authenticated proxy; anonymous rejected" % len(models))
'
}

reseed_owui() {
    [[ -f "$SCRIPT_DIR/owui_connect.py" ]] || return 0
    docker cp "$SCRIPT_DIR/owui_connect.py" snflwr-frontend:/tmp/owui_connect.py >/dev/null 2>&1 || true
    local rc=0
    docker exec snflwr-api printenv INTERNAL_API_KEY 2>/dev/null \
        | docker exec -i snflwr-frontend python /tmp/owui_connect.py >/dev/null 2>&1 || rc=$?
    [[ $rc -eq 0 ]] && docker restart snflwr-frontend >/dev/null 2>&1 || true
}

# =============================================================================
# Component: owui  (Open WebUI image)
# =============================================================================
owui_label() { echo "Open WebUI"; }
owui_current() { get_env_var OWU_IMAGE_TAG "v0.9.6"; }
owui_resolve_target() {
    if [[ -n "${1:-}" ]]; then echo "$1"; else
        python3 "$SCRIPT_DIR/gh_latest_release.py" open-webui/open-webui
    fi
}
owui_pull() { docker pull "ghcr.io/open-webui/open-webui:$1"; }
owui_snapshot() {
    PREV_OWU="$(owui_current)"
    if docker cp "snflwr-frontend:${DB_IN_CONTAINER}" "$DB_BACKUP" >/dev/null 2>&1; then
        info "Backed up webui.db (rollback point)."
    else
        warn "Could not back up webui.db — rollback will restore the image only."
        DB_BACKUP=""
    fi
}
owui_apply() { set_env_var OWU_IMAGE_TAG "$1"; compose up -d open-webui >/dev/null 2>&1; reseed_owui; }
owui_smoke() {
    wait_healthy snflwr-frontend || return 1
    local port; port="$(get_env_var WEBUI_PORT 3000)"
    curl -sf -o /dev/null "http://localhost:${port}/" || { err "web UI not answering on ${port}"; return 1; }
    info "web UI answers HTTP 200"
    proxy_auth_ok || { err "OWU↔proxy auth check failed"; return 1; }
    info "OWU↔proxy auth verified; anonymous calls rejected"
}
owui_restore() {
    set_env_var OWU_IMAGE_TAG "$PREV_OWU"
    # A newer OWU runs Alembic migrations an older image can't read; put the
    # pre-upgrade DB back before the old image starts.
    if [[ -n "$DB_BACKUP" && -f "$DB_BACKUP" ]]; then
        compose up -d --no-start open-webui >/dev/null 2>&1 || true
        docker cp "$DB_BACKUP" "snflwr-frontend:${DB_IN_CONTAINER}" >/dev/null 2>&1 \
            && info "Restored pre-upgrade webui.db." \
            || warn "Could not restore webui.db."
    fi
    owui_apply "$PREV_OWU"
}

# =============================================================================
# Component: ollama  (inference engine image)
# =============================================================================
ollama_label() { echo "Ollama"; }
ollama_current() { get_env_var OLLAMA_IMAGE_TAG "0.30.9"; }
ollama_resolve_target() {
    local t
    if [[ -n "${1:-}" ]]; then t="$1"; else
        t="$(python3 "$SCRIPT_DIR/gh_latest_release.py" ollama/ollama)" || return 1
    fi
    echo "${t#v}"   # Ollama Docker tags have no leading 'v'
}
ollama_pull() { docker pull "ollama/ollama:$1"; }
ollama_snapshot() { PREV_OLLAMA="$(ollama_current)"; }  # models live in the volume; no DB
ollama_apply() { set_env_var OLLAMA_IMAGE_TAG "$1"; compose up -d ollama >/dev/null 2>&1; }
ollama_smoke() {
    wait_healthy snflwr-ollama || return 1
    # version endpoint responds
    if ! docker exec snflwr-ollama ollama --version >/dev/null 2>&1; then
        err "ollama --version failed"; return 1
    fi
    info "ollama responds to --version"
    # the tutor model still loads and generates (and lands on the GPU)
    if ! docker exec snflwr-ollama ollama run snflwr.ai "Say hi in 3 words." >/dev/null 2>&1; then
        err "snflwr.ai failed to generate after upgrade"; return 1
    fi
    local proc; proc="$(docker exec snflwr-ollama ollama ps 2>/dev/null | awk 'NR==2{print $5, $6}')"
    info "snflwr.ai generates (processor: ${proc:-unknown})"
    # the api proxy can still round-trip to ollama
    proxy_auth_ok || { err "proxy↔ollama round-trip failed"; return 1; }
    info "proxy↔ollama round-trip verified"
}
ollama_restore() { set_env_var OLLAMA_IMAGE_TAG "$PREV_OLLAMA"; ollama_apply "$PREV_OLLAMA"; }

# =============================================================================
# Component: model  (tutor backbone — rebuilds the snflwr.ai wrapper)
# =============================================================================
model_label() { echo "Tutor model backbone"; }
model_current() { get_env_var BASE_MODEL "gemma4:e4b"; }
model_resolve_target() {
    if [[ -z "${1:-}" ]]; then
        err "model upgrade needs an explicit base tag, e.g.: guarded_upgrade.sh model gemma4:e4b"
        return 1
    fi
    echo "$1"
}
model_pull() { docker exec snflwr-ollama ollama pull "$1"; }
model_snapshot() {
    PREV_BASE="$(model_current)"
    # Duplicate the current wrapper so we can restore it byte-for-byte.
    docker exec snflwr-ollama ollama cp snflwr.ai "$MODEL_BACKUP_TAG" >/dev/null 2>&1 \
        && info "Snapshotted current snflwr.ai → ${MODEL_BACKUP_TAG}" \
        || warn "Could not snapshot current model; rollback will rebuild from PREV_BASE."
}
model_build() {  # model_build BASE_TAG — rebuild the snflwr.ai wrapper on BASE_TAG
    local base="$1"
    local src="$REPO_DIR/models/Snflwr_AI_Kids.modelfile"
    [[ -f "$src" ]] || { err "Modelfile not found at $src"; return 1; }
    local tmp; tmp="$(mktemp)"
    sed "s|^FROM .*|FROM ${base}|" "$src" > "$tmp"
    docker cp "$tmp" snflwr-ollama:/tmp/snflwr.ai.modelfile >/dev/null 2>&1
    rm -f "$tmp"
    docker exec snflwr-ollama ollama create snflwr.ai -f /tmp/snflwr.ai.modelfile >/dev/null 2>&1
}
model_apply() { set_env_var BASE_MODEL "$1"; model_build "$1"; }
model_smoke() {
    [[ -f "$SCRIPT_DIR/model_canary.py" ]] || { err "model_canary.py missing"; return 1; }
    docker cp "$SCRIPT_DIR/model_canary.py" snflwr-frontend:/tmp/model_canary.py >/dev/null 2>&1 || true
    docker exec snflwr-api printenv INTERNAL_API_KEY 2>/dev/null \
        | docker exec -i snflwr-frontend python /tmp/model_canary.py
}
model_restore() {
    set_env_var BASE_MODEL "$PREV_BASE"
    if docker exec snflwr-ollama ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq "$MODEL_BACKUP_TAG"; then
        docker exec snflwr-ollama ollama cp "$MODEL_BACKUP_TAG" snflwr.ai >/dev/null 2>&1 \
            && info "Restored previous snflwr.ai from ${MODEL_BACKUP_TAG}" \
            || model_build "$PREV_BASE"
    else
        model_build "$PREV_BASE"
    fi
}

# =============================================================================
# Orchestrator
# =============================================================================
COMPONENT="${1:-}"
shift || true
TARGET_ARG=""
DRY_RUN=no
for a in "$@"; do
    case "$a" in
        --dry-run) DRY_RUN=yes ;;
        -h|--help) sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) TARGET_ARG="$a" ;;
    esac
done

case "$COMPONENT" in
    owui|ollama|model) ;;
    ""|-h|--help) sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "Unknown component: '$COMPONENT' (expected owui | ollama | model)"; exit 1 ;;
esac

echo ""
echo "  Guarded upgrade — $(${COMPONENT}_label)"
echo "  ==============================="

if [[ ! -f "$ENV_FILE" ]]; then err "$ENV_FILE not found — run ./deploy.sh first."; exit 1; fi
if ! docker inspect snflwr-frontend &>/dev/null; then
    err "snflwr stack is not running — run ./deploy.sh first."; exit 1
fi

CURRENT="$(${COMPONENT}_current)"
info "Current: ${CURRENT}"

if ! TARGET="$(${COMPONENT}_resolve_target "$TARGET_ARG")"; then
    err "Could not resolve a target version."; exit 1
fi
info "Target:  ${TARGET}"

if [[ "$DRY_RUN" == "yes" ]]; then
    if [[ "$TARGET" == "$CURRENT" ]]; then
        info "Already on ${TARGET}. Dry run — no changes."
    else
        info "Would upgrade ${CURRENT} → ${TARGET}. Dry run — no changes."
    fi
    exit 0
fi

if [[ "$TARGET" == "$CURRENT" ]]; then
    warn "Already on ${TARGET}; re-applying to re-run the smoke test."
fi

# --- pull (if the component pre-pulls) ---------------------------------------
if declare -F "${COMPONENT}_pull" >/dev/null; then
    step "Pulling ${TARGET}"
    if ! "${COMPONENT}_pull" "$TARGET"; then
        err "Pull failed for ${TARGET}. No changes made."; exit 1
    fi
fi

# --- snapshot + apply --------------------------------------------------------
step "Switching to ${TARGET}"
"${COMPONENT}_snapshot"
"${COMPONENT}_apply" "$TARGET"

# --- smoke test --------------------------------------------------------------
step "Smoke testing ${TARGET}"
if "${COMPONENT}_smoke"; then
    echo ""
    info "$(_g "Upgrade successful:") ${CURRENT} → ${TARGET}"
    [[ "$COMPONENT" == "model" ]] && \
        warn "Run 'python -m evals.tutoring.run_eval' for a full quality/safety gate." || \
        warn "For a major version jump, run a manual safety canary before production use."
    exit 0
fi

# --- rollback ----------------------------------------------------------------
echo ""
err "Smoke test FAILED on ${TARGET} — rolling back to ${CURRENT}"
"${COMPONENT}_restore"
step "Re-checking after rollback"
if "${COMPONENT}_smoke"; then
    warn "Rolled back to ${CURRENT}; service restored."
else
    err "Rollback to ${CURRENT} also failed smoke — manual intervention needed (./deploy.sh --logs)."
fi
exit 1
