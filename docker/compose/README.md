# Docker Compose Files

## Main Stack

```bash
docker compose -f docker/compose/docker-compose.yml up -d
```

`docker-compose.yml` runs the full production stack: nginx, Open WebUI, Snflwr API, Ollama, PostgreSQL, Redis, Celery worker, and Celery beat.

## Add-on Files

Combine with the main stack using multiple `-f` flags:

| File | What it adds |
|------|-------------|
| `docker-compose.redis-sentinel.yml` | Redis HA with 3 Sentinel nodes for automatic failover |
| `docker-compose.elk.yml` | ELK stack (Elasticsearch, Logstash, Kibana) for centralized logging |
| `docker-compose.ollama-cluster.yml` | Load-balanced Ollama across multiple GPUs |

Example — main stack with Redis HA:

```bash
docker compose \
  -f docker/compose/docker-compose.yml \
  -f docker/compose/docker-compose.redis-sentinel.yml \
  up -d
```
