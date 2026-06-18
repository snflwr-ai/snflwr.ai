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

    # Re-chown if the directory OR anything inside it isn't owned by the target
    # uid. Checking only the top dir misses stale root-owned files left inside
    # an already-correct dir by an earlier root run (e.g. safety_incidents.log),
    # which silently breaks safety-incident logging. `find ... -print -quit`
    # stops at the first offender, so normal boots stay fast.
    if [ "$(stat -c '%u' "$d")" != "$TARGET_UID" ] || \
       [ -n "$(find "$d" ! -uid "$TARGET_UID" -print -quit 2>/dev/null)" ]; then
        echo "entrypoint: fixing ownership on $d"
        chown -R "$TARGET_UID:$TARGET_GID" "$d"
    fi
done

# Drop privileges and exec the actual command
exec gosu snflwr "$@"
