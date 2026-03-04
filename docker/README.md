# Docker Configuration

Docker images and compose files for snflwr.ai.

## Dockerfiles

| File | Purpose |
|------|---------|
| `Dockerfile` | Production API image (FastAPI + safety pipeline) |
| `Dockerfile.dev` | Development environment |
| `Dockerfile.ollama` | Ollama with Snflwr models baked in (configurable via build args) |

## Compose Files

See [compose/README.md](compose/README.md) for usage.

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Full production stack (all services) |
| `docker-compose.redis-sentinel.yml` | Redis HA add-on |
| `docker-compose.elk.yml` | ELK logging add-on |
| `docker-compose.ollama-cluster.yml` | Multi-GPU Ollama add-on |

## Building Images

```bash
# Build API image
docker build -f docker/Dockerfile -t snflwr-api:latest .

# Build Ollama (both build args required — choose based on hardware)
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=qwen3.5:9b \
  --build-arg SAFETY_MODEL=llama-guard3:1b \
  -t snflwr-ollama:latest .

# Or use the enterprise build script for interactive hardware detection:
#   enterprise/build.sh
```

For enterprise builds with interactive model selection, use `enterprise/build.sh` instead.
