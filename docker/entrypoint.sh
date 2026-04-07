#!/bin/sh
# snflwr.ai API — container entrypoint
#
# The container runs as root ONLY long enough to fix volume ownership, then
# drops privileges to the non-root `snflwr` user via `gosu` before exec'ing
# the real command. This is necessary because Docker named volumes come up
# owned by root by default, and any chown performed in the Dockerfile is
# overwritten as soon as the volume gets mounted at runtime — leaving the
# non-root application unable to write to its own data/log directories.
#
# Idempotent: on subsequent restarts, the chown is skipped if ownership is
# already correct, so normal container boots are fast.

set -eu

TARGET_UID=1000
TARGET_GID=1000

# Directories that come from named volumes and therefore need the
# permission fix on first boot (or whenever the volume is recreated).
for d in /app/data /app/logs; do
    # Directory may not exist yet if a volume wasn't mounted there — skip.
    [ -d "$d" ] || continue

    current_uid="$(stat -c '%u' "$d")"
    if [ "$current_uid" != "$TARGET_UID" ]; then
        echo "entrypoint: fixing ownership on $d (was uid=$current_uid)"
        chown -R "$TARGET_UID:$TARGET_GID" "$d"
    fi
done

# Drop privileges and exec the actual command
exec gosu snflwr "$@"
