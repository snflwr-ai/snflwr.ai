#!/usr/bin/env bash
# Back-compat shim. The guarded upgrader is now a unified, multi-component tool;
# this just forwards to the 'owui' component. Prefer:
#   scripts/guarded_upgrade.sh owui [target] [--dry-run]
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/guarded_upgrade.sh" owui "$@"
