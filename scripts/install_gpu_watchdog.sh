#!/usr/bin/env bash
#
# install_gpu_watchdog.sh — schedule scripts/gpu_watchdog.sh for the DOCKER deploy.
#
# gpu_watchdog.sh self-heals the ollama container losing its GPU, but only
# start_snflwr.sh (the native launcher) ever started it. A box deployed with
# ./deploy.sh had no watchdog at all. Measured 2026-09-16: apt-daily-upgrade ran
# `systemctl daemon-reload` at 06:57, snflwr-ollama lost NVML, and every model
# served from CPU for ~6 hours with a healthy container and no error. Unattended
# upgrades make that recurring, not a one-off, and deploy.sh's own GPU check only
# runs at deploy time.
#
# This installs ONE tagged line in the invoking user's crontab (no sudo needed):
#   */2 * * * * <repo>/scripts/gpu_watchdog.sh >> <repo>/logs/gpu_watchdog.log 2>&1
# Idempotent: the tagged line is replaced, never duplicated, and every other entry
# is preserved. SNFLWR_GPU_WATCHDOG=off removes it instead.
#
# Env: SNFLWR_GPU_WATCHDOG (on|off, default on), CRONTAB_CMD (default crontab; tests
# point it at a fake), GPU_WATCHDOG_SCHEDULE (default */2 * * * *).

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCHDOG="$REPO_DIR/scripts/gpu_watchdog.sh"
LOG="$REPO_DIR/logs/gpu_watchdog.log"
STATE="$REPO_DIR/logs/.gpu_watchdog.last"
CRONTAB_CMD="${CRONTAB_CMD:-crontab}"
SCHEDULE="${GPU_WATCHDOG_SCHEDULE:-*/2 * * * *}"
MODE="${SNFLWR_GPU_WATCHDOG:-on}"
TAG="# snflwr-gpu-watchdog (managed by scripts/install_gpu_watchdog.sh)"

if ! command -v "$CRONTAB_CMD" >/dev/null 2>&1; then
    echo "install_gpu_watchdog: '$CRONTAB_CMD' not found; schedule it manually:" >&2
    echo "  $SCHEDULE $WATCHDOG >> $LOG 2>&1" >&2
    exit 1
fi

# `crontab -l` exits non-zero when the user has no crontab yet; that is an empty
# table, not an error.
current="$("$CRONTAB_CMD" -l 2>/dev/null || true)"
# Drop any previous managed entry (the tag line and the command line after it).
kept="$(printf '%s\n' "$current" | awk -v tag="$TAG" '
    $0 == tag { skip = 1; next }
    skip == 1 { skip = 0; next }
    { print }
' | sed -e '/^$/N;/^\n$/D')"

if [ "$MODE" = "off" ]; then
    printf '%s\n' "$kept" | sed '/^$/d' | "$CRONTAB_CMD" -
    echo "install_gpu_watchdog: removed."
    exit 0
fi

mkdir -p "$REPO_DIR/logs"
# cron runs with a minimal PATH; docker and nvidia-smi live in the system dirs.
line="$SCHEDULE PATH=/usr/local/bin:/usr/bin:/bin GPU_WATCHDOG_STATE=$STATE $WATCHDOG >> $LOG 2>&1"
{
    [ -n "$(printf '%s' "$kept" | tr -d '[:space:]')" ] && printf '%s\n' "$kept"
    printf '%s\n%s\n' "$TAG" "$line"
} | "$CRONTAB_CMD" -
echo "install_gpu_watchdog: scheduled ($SCHEDULE) -> $LOG"
