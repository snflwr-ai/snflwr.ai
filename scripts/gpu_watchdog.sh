#!/usr/bin/env bash
#
# gpu_watchdog.sh — detect + SELF-HEAL Ollama silently falling back to CPU.
#
# The NVIDIA Container Toolkit can lose GPU access inside a *running* container
# after a host `systemctl daemon-reload` / driver update — nvidia-smi inside the
# container starts failing with "Failed to initialize NVML: Unknown Error", and
# Ollama then runs on CPU with NO error of its own. On a children's tutor that
# means ~20x slower answers (slow crisis responses included), undetected.
#
# This watchdog detects exactly that failure — host HAS a GPU but the Ollama
# container can't see it — and restarts the container to restore GPU access,
# with a cooldown so it can't thrash if a restart doesn't help.
#
# Run on the HOST (needs docker + nvidia-smi). Either:
#   * one-shot from cron/systemd, e.g. every 2 min:
#       */2 * * * * /path/to/scripts/gpu_watchdog.sh >> /var/log/snflwr-gpu.log 2>&1
#   * or a loop:  scripts/gpu_watchdog.sh --watch 120
#   (start_snflwr.sh launches the loop automatically on GPU boxes.)
#
# Flags:
#   --dry-run     detect + log, but never restart (safe to test)
#   --watch SECS  run forever, checking every SECS seconds
#
# Env overrides: OLLAMA_CONTAINER (default snflwr-ollama),
#   GPU_WATCHDOG_COOLDOWN (min seconds between auto-restarts, default 300),
#   GPU_WATCHDOG_STATE (cooldown state file, default /tmp/snflwr_gpu_watchdog.last)

set -uo pipefail

CONTAINER="${OLLAMA_CONTAINER:-snflwr-ollama}"
COOLDOWN="${GPU_WATCHDOG_COOLDOWN:-300}"
STATE="${GPU_WATCHDOG_STATE:-/tmp/snflwr_gpu_watchdog.last}"
DRY_RUN=false
WATCH=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --watch)   WATCH="${2:-120}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

log() { echo "[$(date '+%F %T')] gpu_watchdog: $*"; }

host_has_gpu()       { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; }
container_running()  { docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; }
# The precise symptom: nvidia-smi works on the host but FAILS inside the
# container (NVML init error). Works regardless of whether a model is loaded.
container_sees_gpu() { docker exec "$CONTAINER" nvidia-smi -L >/dev/null 2>&1; }

remediate() {
    local now last
    now=$(date +%s)
    last=0; [ -f "$STATE" ] && last=$(cat "$STATE" 2>/dev/null || echo 0)
    if [ $(( now - last )) -lt "$COOLDOWN" ]; then
        log "GPU still unavailable but within cooldown (${COOLDOWN}s) — NOT restarting again. A restart didn't fix it; investigate the host driver / nvidia-container-toolkit."
        return 1
    fi
    if $DRY_RUN; then
        log "[dry-run] would restart $CONTAINER to restore GPU access"
        return 0
    fi
    log "Ollama lost GPU access — restarting $CONTAINER to self-heal."
    echo "$now" > "$STATE" 2>/dev/null || true
    if ! docker restart "$CONTAINER" >/dev/null 2>&1; then
        log "ERROR: 'docker restart $CONTAINER' failed."
        return 1
    fi
    local i
    for i in $(seq 1 30); do
        if container_sees_gpu; then
            log "GPU access RESTORED after restart of $CONTAINER."
            return 0
        fi
        sleep 1
    done
    log "WARNING: GPU still not visible to $CONTAINER after restart — manual intervention needed (host driver / toolkit)."
    return 1
}

check_once() {
    if ! command -v docker >/dev/null 2>&1; then
        log "docker not found — cannot check (run this on the host)."; return 0
    fi
    if ! container_running; then
        log "$CONTAINER is not running — nothing to check."; return 0
    fi
    if ! host_has_gpu; then
        # No host GPU → CPU inference is expected; nothing to heal.
        return 0
    fi
    if container_sees_gpu; then
        log "OK — $CONTAINER has GPU access."
        return 0
    fi
    log "DETECTED: host has a GPU but $CONTAINER cannot see it — Ollama is on CPU."
    remediate
}

if [ "$WATCH" -gt 0 ]; then
    log "watching every ${WATCH}s (container=$CONTAINER, cooldown=${COOLDOWN}s, dry_run=$DRY_RUN)"
    while true; do
        check_once
        sleep "$WATCH"
    done
else
    check_once
fi
