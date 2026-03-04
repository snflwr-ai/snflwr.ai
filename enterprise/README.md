# Enterprise Deployment Guide

Production deployment for schools, districts, and multi-user environments.

For family/home use, see the [root README](../README.md#5-minute-setup) instead.

---

## Prerequisites

- **Server:** Linux recommended (Ubuntu 22.04+ or similar)
- **RAM:** 16 GB+ (8 GB minimum)
- **Docker & Docker Compose:** v2.20+
- **Domain name** with DNS pointing to your server (for HTTPS)
- **SMTP credentials** for email notifications (SendGrid, Mailgun, etc.)

## Quick Start

```bash
# 1. Generate production secrets and .env
python scripts/setup_production.py

# 2. Build Docker images (10-20 min, pulls AI models)
enterprise/build.sh

# 3. Start the stack
docker compose -f docker/compose/docker-compose.yml up -d
```

Then open `https://your-domain.com` and create the first admin account:

```bash
python scripts/bootstrap_admin.py
```

---

## Step-by-Step

### 1. Generate Secrets

The setup wizard generates all credentials and writes `.env.production`:

```bash
python scripts/setup_production.py
```

Choose **production** mode. It will prompt for:
- Domain name
- Database (SQLite or PostgreSQL — PostgreSQL recommended)
- SMTP provider (SendGrid, Mailgun, or custom)
- Redis (recommended for rate limiting)

All secrets are auto-generated with cryptographically secure randomness.

### 2. Build Docker Images

```bash
enterprise/build.sh
```

The build script detects your server hardware and prompts you to select:
- **Chat model** (Qwen3 family) — sized to your server RAM
- **Safety classifier** (Meta Llama Guard) — `llama-guard3:1b` or `llama-guard3:8b`

The LLM safety classifier is **mandatory** for enterprise deployments and cannot be disabled. It runs on every message alongside the deterministic pattern-matching pipeline.

For non-interactive builds (CI/CD):
```bash
enterprise/build.sh --model qwen3.5:27b --safety llama-guard3:8b
enterprise/build.sh --auto    # auto-select based on server RAM
```

This builds:
- **snflwr-api** — FastAPI backend with safety pipeline
- **snflwr-ollama** — Ollama with chat model + Llama Guard safety classifier + custom tutor models
- Pulls nginx, PostgreSQL, and Redis images

### 3. Configure SSL

**Option A: Let's Encrypt (recommended)**

```bash
enterprise/setup-letsencrypt.sh
```

**Option B: Self-signed (testing only)**

```bash
enterprise/nginx/ssl/generate-self-signed.sh
```

Edit `enterprise/nginx/nginx.conf` and replace `snflwr.example.com` with your domain.

### 4. Start the Stack

```bash
docker compose -f docker/compose/docker-compose.yml up -d
```

This starts: nginx, Open WebUI, Snflwr API, Ollama, PostgreSQL, Redis, Celery worker, and Celery beat.

Verify everything is healthy:

```bash
docker compose -f docker/compose/docker-compose.yml ps
```

### 5. Create Admin Account

```bash
python scripts/bootstrap_admin.py
```

### 6. Validate

```bash
python scripts/validate_env.py --env production
```

---

## Optional Add-ons

### Redis High Availability (Sentinel)

For automatic failover with 3 Sentinel nodes:

```bash
docker compose \
  -f docker/compose/docker-compose.yml \
  -f docker/compose/docker-compose.redis-sentinel.yml \
  up -d
```

### Centralized Logging (ELK Stack)

Elasticsearch + Logstash + Kibana for log aggregation:

```bash
docker compose \
  -f docker/compose/docker-compose.yml \
  -f docker/compose/docker-compose.elk.yml \
  up -d
```

### Multi-GPU Ollama Cluster

Load-balanced Ollama across multiple GPUs:

```bash
docker compose \
  -f docker/compose/docker-compose.yml \
  -f docker/compose/docker-compose.ollama-cluster.yml \
  up -d
```

### Monitoring (Prometheus + Grafana)

Monitoring configs are in `enterprise/monitoring/`:

| File | Purpose |
|------|---------|
| `prometheus.yml` | Scrape config for API, Redis, PostgreSQL, nginx |
| `alertmanager.yml` | Alert routing (configure your notification channels) |
| `alerts.yml` | 25 alert rules (system, app, safety, performance) |
| `grafana-dashboard.json` | Pre-built dashboard (import into Grafana) |

### Kubernetes

For large-scale deployments, see [enterprise/k8s/README.md](k8s/README.md).

---

## Scaling

- **Horizontal API scaling:** Adjust `replicas` in compose or use the Kubernetes HPA
- **Database:** PostgreSQL with connection pooling (PgBouncer recommended for 100+ users)
- **AI inference:** Use the Ollama cluster compose for multi-GPU setups

See [docs/deployment/SCALING_GUIDE.md](../docs/deployment/SCALING_GUIDE.md) for details.

## Backups

```bash
# Manual backup
python scripts/backup_database.py backup

# Restore
python scripts/backup_database.py restore --file backups/latest.sql
```

## Further Reading

| Topic | Guide |
|-------|-------|
| Full production checklist | [PRODUCTION_DEPLOYMENT_CHECKLIST.md](../docs/deployment/PRODUCTION_DEPLOYMENT_CHECKLIST.md) |
| PostgreSQL setup | [POSTGRESQL_DEPLOYMENT.md](../docs/deployment/POSTGRESQL_DEPLOYMENT.md) |
| HTTPS/SSL | [HTTPS_DEPLOYMENT_GUIDE.md](../docs/deployment/HTTPS_DEPLOYMENT_GUIDE.md) |
| Monitoring & alerts | [MONITORING_AND_ALERTS.md](../docs/deployment/MONITORING_AND_ALERTS.md) |
| Performance tuning | [PERFORMANCE_OPTIMIZATION.md](../docs/deployment/PERFORMANCE_OPTIMIZATION.md) |
| Incident response | [INCIDENT_RESPONSE_RUNBOOK.md](../docs/deployment/INCIDENT_RESPONSE_RUNBOOK.md) |
| Credentials management | [PRODUCTION_CREDENTIALS_CHECKLIST.md](../docs/deployment/PRODUCTION_CREDENTIALS_CHECKLIST.md) |
