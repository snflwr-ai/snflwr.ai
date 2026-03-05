# snflwr.ai - Production Packaging Guide

## Overview

snflwr.ai packages as a **self-contained Docker stack** with all models, code, and dependencies baked into container images. This enables easy deployment to any Docker-compatible hosting platform.

## Production Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Host Server                       │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Nginx (SSL Termination & Reverse Proxy)             │  │
│  │  Port 80/443 → Internet                              │  │
│  └────────┬────────────────────────────────────┬────────┘  │
│           │                                    │           │
│  ┌────────▼──────────┐              ┌─────────▼────────┐  │
│  │  Open WebUI       │              │  Snflwr API   │  │
│  │  (Pre-built)      │◄────────────►│  (Custom Build)  │  │
│  │  Port: 8080       │              │  Port: 8000      │  │
│  └────────┬──────────┘              └─────────┬────────┘  │
│           │                                    │           │
│           │         ┌──────────────────────────┤           │
│           │         │                          │           │
│  ┌────────▼─────────▼───┐         ┌───────────▼────────┐  │
│  │  Ollama + Models      │         │  PostgreSQL        │  │
│  │  (Custom Build)       │         │  (Official Image)  │  │
│  │  - qwen3.5:9b          │         │  + Redis Cache     │  │
│  │  - llama-guard3:1b    │         │                    │  │
│  │  - safety classifier  │         └────────────────────┘  │
│  │  - snflwr tutor    │                                 │
│  │  - educator assistant │                                 │
│  └───────────────────────┘                                 │
│                                                             │
│  All containers connected via internal Docker network      │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. **Pre-Built Ollama Image with Models**

**Why:** Models are large (1-3GB each). Downloading them on first run causes:
- Slow initial startup (10+ minutes)
- Race conditions (API starts before models ready)
- Network dependency during deployment
- Inconsistent deployments (model versions can change)

**Solution:** Bake models into Ollama Docker image during build time.

**Benefits:**
- ✅ Instant startup (models already present)
- ✅ Consistent deployments (same models every time)
- ✅ Offline deployment capable
- ✅ Version control for models
- ✅ Faster scaling (spin up new instances immediately)

**Dockerfile.ollama:**
```dockerfile
FROM ollama/ollama:latest
# Download models during build
RUN ollama serve & \
    ollama pull llama-guard3:1b && \
    ...
# Models now baked into image layers
```

**Image size:** ~4-6GB (includes all models)

### 2. **Pre-Built Snflwr API Image**

**Why:** Your safety pipeline code should be:
- Version controlled
- Reproducible
- Fast to deploy
- Independent of Open WebUI changes

**Solution:** Separate Docker image for Snflwr API.

**Benefits:**
- ✅ Independent versioning (API v1.0, v2.0, etc.)
- ✅ Can update API without rebuilding Ollama
- ✅ Horizontal scaling (run multiple API instances)
- ✅ Easier testing and CI/CD

### 3. **Official Open WebUI Image**

**Why:** Open WebUI is actively maintained. Using their official image means:
- Automatic security updates
- Bug fixes from upstream
- Community improvements
- Less maintenance burden

**How we customize it:**
- Mount custom middleware as volume (if needed)
- Configure via environment variables
- Point to Snflwr API for safety checks

**Benefits:**
- ✅ Stay up-to-date with Open WebUI releases
- ✅ Minimal custom code to maintain
- ✅ Community support

### 4. **Single Docker Compose Stack**

All services defined in one `docker-compose.yml`:
- Easy to deploy: `docker-compose up -d`
- Easy to update: `docker-compose pull && docker-compose up -d`
- Easy to backup: `docker-compose down && backup volumes`
- Self-documenting: All infrastructure as code

## Packaging Process

### Build Phase (One-time setup or when updating)

```bash
# 1. Configure environment
cp .env.production.example .env.production
nano .env.production  # Set secrets, domains, etc.

# 2. Build images with models baked in
./enterprise/build.sh

# This creates:
# - snflwr-ollama:latest (4-6GB) - Ollama + all models
# - snflwr-api:latest (500MB) - Your safety pipeline
```

**Build time:** 15-30 minutes (mostly downloading models)

### Deployment Phase (Quick - just start containers)

```bash
# Start entire stack
docker-compose -f docker/compose/docker-compose.yml up -d

# First startup: ~30 seconds
# Subsequent startups: ~5 seconds
```

## Distribution Methods

### Method 1: Docker Registry (Recommended for SaaS)

**For:** Deploying to cloud platforms, selling to enterprises

```bash
# Tag images
docker tag snflwr-ollama:latest your-registry.com/snflwr-ollama:v1.0
docker tag snflwr-api:latest your-registry.com/snflwr-api:v1.0

# Push to private registry (Docker Hub, AWS ECR, etc.)
docker push your-registry.com/snflwr-ollama:v1.0
docker push your-registry.com/snflwr-api:v1.0

# Customers deploy with:
docker-compose pull
docker-compose up -d
```

**Advantages:**
- Professional distribution
- Version control (v1.0, v1.1, etc.)
- Automatic updates possible
- Metrics on usage

**Image sizes:**
- Ollama image: 4-6GB
- API image: 500MB
- Total download: ~5-6GB (one-time)

### Method 2: Save Images to File (Self-Hosted/Offline)

**For:** Schools with limited internet, air-gapped environments

```bash
# Save images to tarball
docker save snflwr-ollama:latest | gzip > snflwr-ollama-v1.0.tar.gz
docker save snflwr-api:latest | gzip > snflwr-api-v1.0.tar.gz

# Package includes:
# - Image tarballs (5-6GB total compressed)
# - docker-compose.yml
# - .env.production.example
# - init-db.sql
# - nginx configs
# - Documentation

# Customer loads with:
docker load < snflwr-ollama-v1.0.tar.gz
docker load < snflwr-api-v1.0.tar.gz
docker-compose up -d
```

**Advantages:**
- Works offline
- One-time transfer (USB drive, download)
- No ongoing connection to your servers
- Full privacy

**Package size:** ~4-5GB compressed

### Method 3: Cloud Marketplace (AWS/Azure/GCP)

**For:** Selling to enterprises via cloud marketplaces

Package as:
- **AWS AMI** - Pre-configured EC2 instance
- **Azure VM Image** - Pre-configured Azure VM
- **GCP Instance Template** - Pre-configured GCE instance

**Advantages:**
- One-click deployment
- Integrated billing
- Enterprise credibility
- Automatic infrastructure setup

## Deployment Targets

### Target 1: Single Server (Small Schools/Families)

**Specs:**
- 16GB RAM
- 4 CPU cores
- 100GB SSD
- (Optional) NVIDIA GPU

**Cost:** $40-80/month
**Capacity:** 50-200 concurrent users

**Deployment:**
```bash
# One command
docker-compose -f docker/compose/docker-compose.yml up -d
```

### Target 2: Cloud Platform (Medium Scale)

**Platforms:**
- DigitalOcean App Platform
- AWS ECS/Fargate
- Google Cloud Run
- Azure Container Instances

**Deployment:**
- Push images to registry
- Configure platform to pull and run
- Platform handles load balancing, scaling

**Cost:** $100-500/month
**Capacity:** 500-5000 users

### Target 3: Kubernetes (Large Scale/Enterprise)

**For:** School districts, multi-tenant SaaS

```yaml
# Deploy to K8s cluster
kubectl apply -f kubernetes/
```

**Features:**
- Auto-scaling
- Zero-downtime updates
- Multi-region deployment
- High availability

**Cost:** $500-2000+/month
**Capacity:** 10,000+ users

## Update Process

### Updating Models or Code

```bash
# 1. Rebuild images with updates
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=qwen3.5:9b --build-arg SAFETY_MODEL=llama-guard3:1b \
  -t snflwr-ollama:v1.1 .
docker build -f docker/Dockerfile -t snflwr-api:v1.1 .

# 2. Update docker-compose to use new versions
# 3. Rolling update (zero downtime)
docker-compose up -d --no-deps --build snflwr-api
docker-compose up -d --no-deps --build ollama
```

**Downtime:** 0 seconds (rolling update)

## Backup Strategy

```bash
# Backup user data (PostgreSQL)
docker exec snflwr-db pg_dump -U snflwr snflwr_db > backup.sql

# Backup Open WebUI data
docker run --rm -v snflwr_open-webui-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/webui-backup.tar.gz -C /data .

# Restore process is documented in DEPLOYMENT.md
```

## Security Considerations

### 1. **Secrets Management**

**Development:**
```bash
# .env.production (not committed to git)
WEBUI_SECRET_KEY=random-generated-key
POSTGRES_PASSWORD=strong-password
SMTP_PASSWORD=email-password
```

**Production:**
- Use Docker Secrets (Swarm)
- Use Kubernetes Secrets (K8s)
- Use AWS Secrets Manager (ECS)
- Use Azure Key Vault (ACI)

### 2. **Network Isolation**

All services on internal Docker network:
- Only Nginx exposed to internet (ports 80/443)
- Database, Ollama, API not directly accessible
- Inter-service communication encrypted (optional)

### 3. **SSL/TLS**

```bash
# Automated with Let's Encrypt
certbot certonly --standalone -d snflwr.ai
```

Or mount certificates as volume:
```yaml
volumes:
  - ./ssl/cert.pem:/etc/nginx/ssl/cert.pem:ro
  - ./ssl/key.pem:/etc/nginx/ssl/key.pem:ro
```

## Monitoring & Logging

### Logs

```bash
# View all logs
docker-compose logs -f

# View specific service
docker-compose logs -f snflwr-api

# Export logs to file
docker-compose logs --no-color > logs-$(date +%Y%m%d).txt
```

### Metrics

Add Prometheus + Grafana (optional):
```yaml
# docker-compose.yml
prometheus:
  image: prom/prometheus
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml

grafana:
  image: grafana/grafana
  ports:
    - "3001:3000"
```

## Cost Analysis

### Self-Hosted (One-time build + hosting)

**Initial Setup:**
- Development time: $0 (you've already built it)
- Image building: Free (local or CI/CD)

**Ongoing:**
- VPS (16GB RAM): $40-80/month
- Domain: $10/year
- SSL: Free (Let's Encrypt)
- **Total: ~$50-100/month**

### SaaS Distribution

**Per Customer Deployment:**
- Docker images: Already built (shared across customers)
- Each customer runs own instance: $40-80/month (their cost)
- You charge: $200-500/month
- **Margin: $120-420/month per customer**

## Comparison to Traditional Deployment

| Aspect | Docker Packaging | Traditional (npm/Python) |
|--------|------------------|--------------------------|
| Setup time | 5 minutes | 1-2 hours |
| Consistency | Perfect (containers) | Variable (dependencies) |
| Update process | `docker-compose pull && up -d` | Complex migration scripts |
| Portability | Run anywhere (Docker) | OS-specific issues |
| Scaling | Add replicas easily | Complex load balancing |
| Rollback | Change image tag | Restore backups |
| Model management | Baked in | Download on startup |

## Recommended Package for Different Customers

### 1. **Individual Families / Homeschool**
- **Method:** Docker Compose (single server)
- **Distribution:** Documentation + support
- **Pricing:** One-time $99 + $9.99/month hosting guide

### 2. **Small Schools (50-500 students)**
- **Method:** Cloud marketplace or managed hosting
- **Distribution:** One-click deploy on DigitalOcean/AWS
- **Pricing:** $200-500/month subscription

### 3. **School Districts (1000+ students)**
- **Method:** Kubernetes or private cloud
- **Distribution:** Enterprise support + custom deployment
- **Pricing:** $2000-5000/month + setup fee

## Summary

**Production packaging uses:**
1. ✅ Pre-built Docker images with models baked in
2. ✅ Docker Compose for orchestration
3. ✅ Nginx for SSL and reverse proxy
4. ✅ PostgreSQL for data persistence
5. ✅ Redis for caching and rate limiting

**Deployment is:**
- 🚀 Fast (5 minutes from zero to running)
- 🔒 Secure (network isolation, SSL, secrets)
- 📦 Portable (runs anywhere Docker runs)
- 🔄 Updateable (rolling updates, zero downtime)
- 💰 Cost-effective ($50-100/month for small scale)

**Next Steps:**
1. Run `./enterprise/build.sh` to create images
2. Test deployment locally
3. Deploy to staging environment
4. Get feedback from beta users
5. Launch!
