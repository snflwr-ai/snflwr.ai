#!/usr/bin/env bash
#
# Guarded Open WebUI upgrade with automatic rollback.
#
# Pulls a target Open WebUI image, swaps the running container to it, re-seeds
# the proxy credential, then runs a smoke test. If the smoke test fails (or the
# pull fails), the previous image tag is restored and the container rolled back
# — so an OWU upgrade can never leave the kids' app in a broken state.
#
# Usage:
#   scripts/owui_upgrade.sh                 # upgrade to latest stable release
#   scripts/owui_upgrade.sh v0.9.6          # upgrade to a specific tag
#   scripts/owui_upgrade.sh --dry-run       # show target version, change nothing
#
# What the smoke test verifies (and what it does NOT):
#   ✓ open-webui container reaches "healthy"
#   ✓ the web UI answers HTTP 200
#   ✓ OWU still authenticates to the snflwr-api proxy (model list returns
#     models, not 401) — the integration most likely to break on an upgrade
#   ✓ the proxy still REJECTS unauthenticated calls (security gate intact)
#   ✗ it does NOT exercise the safety pipeline end-to-end. That risk is low by
#     design: the proxy's admin/student split is fail-closed, so if a new OWU
#     stopped forwarding the user-role header, every request degrades to the
#     student (more filtering, never less). For a MAJOR version jump, still run
#     a manual safety canary before trusting it in front of children.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.home"
COMPOSE_BASE="$REPO_DIR/docker/compose/docker-compose.home.yml"
COMPOSE_GPU="$REPO_DIR/docker/compose/docker-compose.gpu.yml"
IMAGE_REPO="ghcr.io/open-webui/open-webui"
SMOKE_TIMEOUT=120
DB_BACKUP="${TMPDIR:-/tmp}/snflwr-owui-webui.db.pre-upgrade.bak"

# --- pretty output -----------------------------------------------------------
if [[ -t 1 ]]; then
    _g() { printf '\033[32m%s\033[0m' "$1"; }
    _r() { printf '\033[31m%s\033[0m' "$1"; }
    _y() { printf '\033[33m%s\033[0m' "$1"; }
else
    _g() { printf '%s' "$1"; }; _r() { printf '%s' "$1"; }; _y() { printf '%s' "$1"; }
fi
info()  { echo "  $(_g '[+]') $*"; }
warn()  { echo "  $(_y '[!]') $*"; }
err()   { echo "  $(_r '[x]') $*" >&2; }
step()  { echo ""; echo "  $1"; echo "  ${1//?/-}"; }

DRY_RUN=no
TARGET_ARG=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=yes ;;
        -h|--help) sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) TARGET_ARG="$arg" ;;
    esac
done

# --- compose helper ----------------------------------------------------------
compose() {
    local files=(-f "$COMPOSE_BASE")
    [[ -f "$COMPOSE_GPU" ]] && files+=(-f "$COMPOSE_GPU")
    docker compose "${files[@]}" --env-file "$ENV_FILE" "$@"
}

# --- read/write OWU_IMAGE_TAG in .env.home -----------------------------------
get_current_tag() {
    if [[ -f "$ENV_FILE" ]] && grep -q '^OWU_IMAGE_TAG=' "$ENV_FILE"; then
        grep '^OWU_IMAGE_TAG=' "$ENV_FILE" | head -1 | cut -d= -f2
    else
        echo "v0.8.12"  # compose default
    fi
}

set_tag() {
    local tag="$1"
    if grep -q '^OWU_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^OWU_IMAGE_TAG=.*|OWU_IMAGE_TAG=${tag}|" "$ENV_FILE"
    else
        echo "OWU_IMAGE_TAG=${tag}" >> "$ENV_FILE"
    fi
}

# --- smoke test --------------------------------------------------------------
# Returns 0 if the upgraded OWU is healthy and integrated, non-zero otherwise.
smoke_test() {
    local webui_port
    webui_port="$(grep '^WEBUI_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
    webui_port="${webui_port:-3000}"

    # 1) container healthy
    local elapsed=0
    printf "  waiting for open-webui health"
    while [[ "$(docker inspect snflwr-frontend --format '{{.State.Health.Status}}' 2>/dev/null)" != "healthy" ]]; do
        sleep 3; elapsed=$((elapsed + 3)); printf "."
        if [[ $elapsed -ge $SMOKE_TIMEOUT ]]; then
            echo ""; err "open-webui did not become healthy within ${SMOKE_TIMEOUT}s"; return 1
        fi
    done
    echo ""
    info "open-webui is healthy"

    # 2) web UI answers 200
    if ! curl -sf -o /dev/null "http://localhost:${webui_port}/"; then
        err "web UI did not answer HTTP 200 on port ${webui_port}"; return 1
    fi
    info "web UI answers HTTP 200"

    # 3) OWU still authenticates to the proxy AND the proxy still rejects
    #    anonymous calls. The key is piped to the checker's stdin from
    #    snflwr-api so no secret is echoed. The checker script is passed via
    #    `python -c` (NOT a heredoc) so stdin stays free to carry the key.
    local smoke_py
    smoke_py="$(cat <<'PYEOF'
import sys, urllib.request, urllib.error, json
key = sys.stdin.read().strip()
base = "http://snflwr-api:39150/api/tags"

# 3a) authenticated request returns models
req = urllib.request.Request(base, headers={"Authorization": "Bearer " + key})
try:
    models = json.load(urllib.request.urlopen(req, timeout=10)).get("models", [])
except Exception as e:
    print("FAIL: authenticated /api/tags errored: %s" % e, file=sys.stderr); sys.exit(1)
if not models:
    print("FAIL: authenticated /api/tags returned no models", file=sys.stderr); sys.exit(1)

# 3b) anonymous request is rejected (security gate intact)
try:
    urllib.request.urlopen(base, timeout=10)
    print("FAIL: proxy accepted an UNAUTHENTICATED /api/tags", file=sys.stderr); sys.exit(1)
except urllib.error.HTTPError as e:
    if e.code != 401:
        print("FAIL: anonymous /api/tags returned %s, expected 401" % e.code, file=sys.stderr); sys.exit(1)
except Exception as e:
    print("FAIL: anonymous /api/tags errored unexpectedly: %s" % e, file=sys.stderr); sys.exit(1)

print("OK: %d model(s) via authenticated proxy; anonymous rejected" % len(models))
PYEOF
)"
    if ! docker exec snflwr-api printenv INTERNAL_API_KEY \
            | docker exec -i snflwr-frontend python -c "$smoke_py"; then
        err "proxy integration check failed"; return 1
    fi
    info "OWU↔proxy auth verified; anonymous calls rejected"
    return 0
}

# --- bring OWU up on the current tag -----------------------------------------
apply_owu() {
    compose up -d open-webui >/dev/null 2>&1
    # Re-seed the proxy credential (idempotent) in case the upgrade reset config.
    if [[ -f "$SCRIPT_DIR/owui_connect.py" ]]; then
        docker cp "$SCRIPT_DIR/owui_connect.py" snflwr-frontend:/tmp/owui_connect.py >/dev/null 2>&1 || true
        local rc=0
        docker exec snflwr-api printenv INTERNAL_API_KEY 2>/dev/null \
            | docker exec -i snflwr-frontend python /tmp/owui_connect.py >/dev/null 2>&1 || rc=$?
        # rc 0 = re-seeded (needs restart to load); 2 = already correct
        if [[ $rc -eq 0 ]]; then
            docker restart snflwr-frontend >/dev/null 2>&1 || true
        fi
    fi
}

# --- webui.db backup / restore ----------------------------------------------
# A newer Open WebUI runs Alembic migrations that an older image can't read, so
# a plain image-tag rollback would fail on exactly the major-version jumps this
# guard exists for. Snapshot the DB before upgrading and restore it on rollback.
DB_IN_CONTAINER="/app/backend/data/webui.db"

backup_db() {
    if docker cp "snflwr-frontend:${DB_IN_CONTAINER}" "$DB_BACKUP" >/dev/null 2>&1; then
        info "Backed up webui.db (rollback point)."
    else
        warn "Could not back up webui.db — rollback will restore the image only."
        DB_BACKUP=""
    fi
}

restore_db() {
    [[ -n "$DB_BACKUP" && -f "$DB_BACKUP" ]] || return 0
    # Create (but don't start) the container so the volume exists, drop the
    # pre-upgrade DB back in, then let apply_owu start it on the old image.
    compose up -d --no-start open-webui >/dev/null 2>&1 || true
    if docker cp "$DB_BACKUP" "snflwr-frontend:${DB_IN_CONTAINER}" >/dev/null 2>&1; then
        info "Restored pre-upgrade webui.db."
    else
        warn "Could not restore webui.db — DB may be on the upgraded schema."
    fi
}

# =============================================================================
echo ""
echo "  Open WebUI — guarded upgrade"
echo "  ==========================="

if [[ ! -f "$ENV_FILE" ]]; then
    err "$ENV_FILE not found — run ./deploy.sh first."; exit 1
fi
if ! docker inspect snflwr-frontend &>/dev/null; then
    err "snflwr-frontend is not running — run ./deploy.sh first."; exit 1
fi

CURRENT_TAG="$(get_current_tag)"
info "Current Open WebUI: ${CURRENT_TAG}"

# --- resolve target ----------------------------------------------------------
if [[ -n "$TARGET_ARG" ]]; then
    TARGET_TAG="$TARGET_ARG"
    info "Target (explicit): ${TARGET_TAG}"
else
    info "Resolving latest stable release..."
    if ! TARGET_TAG="$(python3 "$SCRIPT_DIR/owui_version.py")"; then
        err "Could not resolve latest stable release. Pass a tag explicitly."; exit 1
    fi
    info "Target (latest stable): ${TARGET_TAG}"
fi

if [[ "$TARGET_TAG" == "$CURRENT_TAG" ]]; then
    info "Already on ${TARGET_TAG} — nothing to do."
    [[ "$DRY_RUN" == "yes" ]] && exit 0
    # Still allow re-applying (e.g. to re-run smoke) but make it explicit.
    warn "Re-applying ${TARGET_TAG} to re-run the smoke test."
fi

if [[ "$DRY_RUN" == "yes" ]]; then
    info "Dry run — would upgrade ${CURRENT_TAG} → ${TARGET_TAG}. No changes made."
    exit 0
fi

# --- pull target -------------------------------------------------------------
# NOTE: do not pipe `docker pull` into tail — the pipeline would mask pull's
# exit code and a bad/nonexistent tag would slip through as success.
step "Pulling ${IMAGE_REPO}:${TARGET_TAG}"
if ! docker pull "${IMAGE_REPO}:${TARGET_TAG}"; then
    err "Pull failed for ${IMAGE_REPO}:${TARGET_TAG}. No changes made."; exit 1
fi

# --- swap to target ----------------------------------------------------------
step "Switching open-webui to ${TARGET_TAG}"
backup_db
set_tag "$TARGET_TAG"
apply_owu

# --- smoke test --------------------------------------------------------------
step "Smoke testing ${TARGET_TAG}"
if smoke_test; then
    echo ""
    info "$(_g "Upgrade successful:") ${CURRENT_TAG} → ${TARGET_TAG}"
    warn "For a major version jump, run a manual safety canary before production use."
    exit 0
fi

# --- rollback ----------------------------------------------------------------
echo ""
err "Smoke test FAILED on ${TARGET_TAG} — rolling back to ${CURRENT_TAG}"
set_tag "$CURRENT_TAG"
restore_db   # undo any schema migration the new image applied
apply_owu
if smoke_test; then
    warn "Rolled back to ${CURRENT_TAG}; service restored."
else
    err "Rollback to ${CURRENT_TAG} also failed smoke test — manual intervention needed."
    err "Check: ./deploy.sh --logs"
fi
exit 1
