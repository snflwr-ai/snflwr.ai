# Self-hosted Langfuse Observability (Enterprise)

snflwr-api can emit **metadata-only** LLM traces to a self-hosted Langfuse running
in the enterprise stack. It is **off by default** and never sends chat content.

## What is captured (and what is NOT)
Captured: model, per-stage latency, token counts (when the model returns them),
safety verdict (category / severity / which layer blocked / allowed-or-blocked),
an **age-band** (`<13` / `13-17` / `18+`), and a **salted one-way hash** of the
profile id (for per-child grouping).
NEVER captured: prompt or response text, exact age, raw profile/user id, or email.

## Enabling
1. Set the Langfuse service secrets in `.env.production`: `LANGFUSE_DB_PASSWORD`,
   `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY`
   (each `python -c 'import secrets; print(secrets.token_hex(32))'`), and
   `LANGFUSE_HASH_SALT`.
2. Bring up the service: `docker compose -f docker/compose/docker-compose.yml --profile observability up -d langfuse`.
   It runs its own migrations against the dedicated `langfuse` database.

   > **Already-initialized Postgres:** the `langfuse` role/database are created by a
   > Postgres init hook that runs **only on first cluster init**. If your cluster was
   > first initialized before `LANGFUSE_DB_PASSWORD` was set, the hook skipped
   > gracefully and the role/db do not exist. Create them once by hand:
   >
   > ```sh
   > docker exec -e PGPASSWORD=$POSTGRES_PASSWORD snflwr-db psql -U snflwr -d snflwr_db \
   >   -v lf_pw="$LANGFUSE_DB_PASSWORD" \
   >   -c "CREATE ROLE langfuse LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'lf_pw';" \
   >   -c "CREATE DATABASE langfuse OWNER langfuse;"
   > ```
3. Reach the UI to create your project keys. The service uses `expose: 3000`
   (internal to the Docker network — NOT published to the host), so first make
   it reachable, either by:
   - temporarily adding `ports: ["127.0.0.1:3000:3000"]` to the `langfuse`
     service and re-running `up -d langfuse`, then SSH-forwarding the host port:
     `ssh -L 3000:localhost:3000 <host>` and visiting http://localhost:3000; or
   - adding the nginx route from "Exposing the UI" below.
   Create an account + project and copy the project's public/secret keys (then
   remove the temporary `ports:` mapping if you added one).
4. Put the keys in `.env.production` as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`,
   set `LANGFUSE_ENABLED=true`, and restart snflwr-api:
   `docker compose -f docker/compose/docker-compose.yml up -d snflwr-api`.

## Exposing the UI (optional)
By default Langfuse is internal-only (no nginx route). To expose it, add an nginx
`location` to `enterprise/nginx/nginx.conf` proxying to `http://langfuse:3000`, ideally
behind auth — it is an operator tool, not a parent/child surface.

## Disable / rollback
Set `LANGFUSE_ENABLED=false` and restart snflwr-api (tracing no-ops immediately).
Remove the `langfuse` service to stop it; `DROP DATABASE langfuse;` to reclaim space.

## Privacy stance
Self-hosted + metadata-only means no child content or raw identifier ever leaves
snflwr-api into the observability store, satisfying the COPPA/FERPA constraint
regardless of where Langfuse runs.
