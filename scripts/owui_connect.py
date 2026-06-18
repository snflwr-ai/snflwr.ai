#!/usr/bin/env python3
"""Seed the snflwr-api proxy bearer credential into Open WebUI's database.

Open WebUI (v0.8.x) does NOT read the Ollama connection key from an environment
variable: its ``OLLAMA_API_CONFIGS`` PersistentConfig is hardcoded to a ``{}``
default (config.py:1039) and only ever loads the key from ``webui.db``
(``ollama.api_configs."<idx>".key``) or the admin "Manage Connections" UI.

So the ``OLLAMA_API_KEY`` env set on the open-webui service is silently ignored,
the connection sends no ``Authorization`` header, the proxy answers ``401``, and
the model dropdown shows "No models available" (chat would 401 too).

This script writes the key directly into the connection config so a *fresh*
``open-webui-data`` volume can authenticate to the proxy. It is idempotent:
re-running with the same key is a no-op.

Run *inside* the open-webui container with the key on stdin::

    docker exec snflwr-api printenv INTERNAL_API_KEY \\
        | docker exec -i snflwr-frontend python /tmp/owui_connect.py

Exit codes (consumed by deploy.sh):
    0  config changed  -> caller should restart open-webui to apply
    2  already correct -> no restart needed
    1  error           -> caller should warn
"""
import json
import sqlite3
import sys
import time

DB_PATH = "/app/backend/data/webui.db"
PROXY_URL = "http://snflwr-api:39150"


def main() -> int:
    key = sys.stdin.read().strip()
    if not key:
        print("ERROR: empty INTERNAL_API_KEY on stdin", file=sys.stderr)
        return 1

    # The `config` table is created by Alembic migrations during boot, but on a
    # brand-new volume Open WebUI does not write a config *row* until the first
    # admin signup or settings change — so we must INSERT one ourselves. Retry
    # only covers the brief window before migrations finish creating the table.
    con = None
    cur = None
    for _ in range(15):
        try:
            con = sqlite3.connect(DB_PATH)
            cur = con.cursor()
            cur.execute("SELECT id, data FROM config ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            break
        except sqlite3.OperationalError:
            time.sleep(2)  # config table not migrated yet
    else:
        print("ERROR: Open WebUI config table never appeared (DB not ready)", file=sys.stderr)
        return 1

    if row:
        config_id, data = row
        config = json.loads(data)
    else:
        # Fresh volume: no row yet. A partial config is valid — Open WebUI's
        # PersistentConfig reads each key by path and falls back to code
        # defaults for anything absent (config.py get_config_value).
        config_id, config = None, {"version": 0, "ui": {}}

    ollama = config.get("ollama") or {}
    existing_key = (ollama.get("api_configs") or {}).get("0", {}).get("key")
    already_ok = (
        existing_key == key
        and ollama.get("enable") is True
        and PROXY_URL in (ollama.get("base_urls") or [])
    )
    if already_ok:
        print("Open WebUI already connected to proxy.")
        return 2

    ollama["enable"] = True
    ollama["base_urls"] = [PROXY_URL]
    api_configs = ollama.get("api_configs") or {}
    api_configs["0"] = {**api_configs.get("0", {}), "enable": True, "key": key}
    ollama["api_configs"] = api_configs
    config["ollama"] = ollama

    if config_id is None:
        cur.execute("INSERT INTO config (data, version) VALUES (?, 0)", (json.dumps(config),))
        action = "Inserted"
    else:
        cur.execute("UPDATE config SET data = ? WHERE id = ?", (json.dumps(config), config_id))
        action = "Seeded"
    con.commit()
    print(f"{action} proxy credential into Open WebUI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
