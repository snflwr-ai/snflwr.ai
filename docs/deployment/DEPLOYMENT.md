---
---

# snflwr.ai Production Deployment Guide

## Overview

snflwr.ai uses a containerized architecture with Docker Compose for easy deployment. All components run in isolated containers and communicate via an internal Docker network.

## Architecture

```
Internet → Nginx (SSL) → Open WebUI Frontend → Snflwr API → Ollama
                              ↓                      ↓
                         PostgreSQL           PostgreSQL
```

## Components

1. **Nginx** - Reverse proxy, SSL termination, rate limiting
2. **Open WebUI** - Svelte frontend (pre-built Docker image)
3. **Snflwr API** - Python FastAPI backend with 5-stage safety pipeline
4. **Ollama** - Local AI inference engine
5. **PostgreSQL** - Database for user data, profiles, incidents
6. **Redis** - Caching, rate limiting, and Celery message broker
7. **Celery Worker** - Background task processing (emails, AI batch jobs)
8. **Celery Beat** - Scheduled task scheduler (cleanup, daily digests)

## Prerequisites

- Linux server (Ubuntu 22.04 recommended) or Windows Server
- Docker Engine 24.0+
- Docker Compose 2.0+
- 16GB+ RAM
- NVIDIA GPU (optional but recommended for faster inference)
- Domain name pointing to your server

## Initial Setup

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/snflwr-ai.git
cd snflwr-ai
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.production.example .env.production

# Edit with your values
nano .env.production
```

**Required changes:**
- `WEBUI_SECRET_KEY` - Generate with: `openssl rand -hex 32`
- `POSTGRES_PASSWORD` - Strong database password
- `DOMAIN` - Your domain name
- `SMTP_*` - Email settings for parent alerts

### 3. Setup SSL Certificates

**Option A: Let's Encrypt (Recommended)**

```bash
# Install certbot
sudo apt install certbot

# Get certificate
sudo certbot certonly --standalone -d snflwr.ai -d www.snflwr.ai

# Copy to project
sudo cp /etc/letsencrypt/live/snflwr.ai/fullchain.pem ./ssl/cert.pem
sudo cp /etc/letsencrypt/live/snflwr.ai/privkey.pem ./ssl/key.pem
```

**Option B: Self-Signed (Development Only)**

```bash
mkdir ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ssl/key.pem -out ssl/cert.pem
```

### 4. Initialize Database

```bash
# Create init script
cat > init-db.sql << 'EOF'
-- Create tables
CREATE TABLE IF NOT EXISTS parent_accounts (
    parent_id VARCHAR(50) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    subscription_tier VARCHAR(20) DEFAULT 'FREE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS child_profiles (
    profile_id VARCHAR(50) PRIMARY KEY,
    parent_id VARCHAR(50) REFERENCES parent_accounts(parent_id),
    name VARCHAR(100) NOT NULL,
    age INTEGER,
    grade_level VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS safety_incidents (
    incident_id VARCHAR(50) PRIMARY KEY,
    profile_id VARCHAR(50) REFERENCES child_profiles(profile_id),
    incident_type VARCHAR(50),
    severity VARCHAR(20),
    input_text TEXT,
    model_response TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversation_limits (
    profile_id VARCHAR(50) PRIMARY KEY REFERENCES child_profiles(profile_id),
    conversations_this_week INTEGER DEFAULT 0,
    week_start_date DATE,
    last_checked TIMESTAMP
);

-- Create indexes
CREATE INDEX idx_incidents_profile ON safety_incidents(profile_id);
CREATE INDEX idx_incidents_timestamp ON safety_incidents(timestamp);
CREATE INDEX idx_profiles_parent ON child_profiles(parent_id);
EOF
```

### 5. Pull Ollama Models

```bash
# Start just Ollama first
docker-compose -f docker/compose/docker-compose.yml up -d ollama

# Wait for Ollama to start
sleep 10

# Pull required models
docker exec snflwr-ollama ollama pull qwen3.5:9b
docker exec snflwr-ollama ollama pull llama-guard3:1b

# Build Snflwr model from modelfile
docker cp models/ snflwr-ollama:/tmp/models/
docker exec snflwr-ollama ollama create snflwr.ai -f /tmp/models/Snflwr_AI_Kids.modelfile
```

## Deployment

### Start All Services

```bash
# Build and start all containers
docker-compose -f docker/compose/docker-compose.yml up -d --build

# Check logs
docker-compose -f docker/compose/docker-compose.yml logs -f
```

### Verify Services

```bash
# Check all containers are running
docker ps

# Test endpoints
curl http://localhost/health        # Nginx
curl http://localhost:8000/health   # Snflwr API
curl http://localhost:11434/        # Ollama
```

### Access Application

Open browser to: `https://your-domain.com`

**First-time setup:**
1. Create admin account
2. Configure settings
3. Create test child profile

## Background Tasks (Celery)

The production-complete stack includes Celery for background task processing. This is **required** for:

- **Email notifications** - Parent safety alerts, consent requests
- **Scheduled cleanup** - Prevents database bloat (IMPORTANT for compliance)
- **Daily safety digests** - Aggregated incident reports

### Scheduled Tasks (Celery Beat)

These tasks run automatically when Celery Beat is running:

| Task | Schedule | Purpose |
|------|----------|---------|
| `cleanup_old_messages` | Every 6 hours | Remove old conversation messages |
| `cleanup_old_sessions` | Every 12 hours | Clear expired auth sessions |
| `cleanup_old_incidents` | Daily | Archive old safety incidents |
| `send_daily_safety_digests` | Daily | Email digest to parents |

### Verify Celery is Running

```bash
# Check Celery workers and beat scheduler
docker ps | grep celery

# View Celery logs
docker-compose -f docker/compose/docker-compose.yml logs -f celery-worker celery-beat

# Test task execution
docker exec snflwr-celery-worker celery -A utils.celery_config inspect active
```

### Flower Monitoring (Optional)

For Celery task monitoring UI, use the separate Celery compose file:

```bash
docker compose -f docker/compose/docker-compose.yml up -d flower
# Access at http://localhost:5555
```

## Redis High Availability (Sentinel)

For production deployments requiring high availability, use Redis Sentinel for automatic failover.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Redis Sentinel Cluster                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   Sentinel-1 ─────────────────── Sentinel-2                 │
│       │                               │                     │
│       └───────────┬───────────────────┘                     │
│                   │                                         │
│              Sentinel-3                                     │
│                   │                                         │
│          ┌───────┴───────┐                                  │
│          │               │                                  │
│    Redis Master ──► Redis Replica-1                         │
│          │               │                                  │
│          └───────┬───────┘                                  │
│                  │                                          │
│           Redis Replica-2                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Enable Redis Sentinel

1. **Set environment variables:**
```bash
# .env.production
REDIS_SENTINEL_ENABLED=true
REDIS_SENTINEL_HOSTS=redis-sentinel-1:26379,redis-sentinel-2:26379,redis-sentinel-3:26379
REDIS_SENTINEL_MASTER=mymaster
REDIS_PASSWORD=your-secure-redis-password
```

2. **Start with Sentinel compose file:**
```bash
# Start production stack with Sentinel
docker-compose -f docker/compose/docker-compose.yml \
               -f docker-compose.redis-sentinel.yml up -d
```

### Verify Sentinel Status

```bash
# Check master info
docker exec snflwr-sentinel-1 redis-cli -p 26379 sentinel master mymaster

# Check replicas
docker exec snflwr-sentinel-1 redis-cli -p 26379 sentinel replicas mymaster

# Check Sentinel nodes
docker exec snflwr-sentinel-1 redis-cli -p 26379 sentinel sentinels mymaster

# Monitor failover events
docker logs -f snflwr-sentinel-1
```

### Automatic Failover

Sentinel automatically handles failover when:
- Master is unreachable for 5 seconds (configurable)
- 2 out of 3 Sentinel nodes agree on failover

The application automatically reconnects to the new master via Sentinel.

### Health Check Response (Sentinel Mode)

```json
{
  "checks": {
    "redis": {
      "status": "healthy",
      "mode": "sentinel",
      "master": "172.20.0.5:6379",
      "slave_count": 2,
      "sentinel_nodes": 3,
      "failovers": 0
    }
  }
}
```

## Monitoring

### View Logs

```bash
# All services
docker-compose -f docker/compose/docker-compose.yml logs -f

# Specific service
docker-compose -f docker/compose/docker-compose.yml logs -f snflwr-api

# Last 100 lines
docker-compose -f docker/compose/docker-compose.yml logs --tail=100
```

### Check Resource Usage

```bash
docker stats
```

### Prometheus Metrics

snflwr.ai exposes Prometheus-compatible metrics at the `/metrics` endpoint for observability.

#### Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `snflwr_http_requests_total` | Counter | Total HTTP requests by method, endpoint, status |
| `snflwr_http_request_duration_seconds` | Histogram | Request latency distribution |
| `snflwr_circuit_breaker_state` | Gauge | Circuit breaker state (0=closed, 1=open, 2=half_open) |
| `snflwr_circuit_breaker_requests_total` | Counter | Requests through circuit breaker |
| `snflwr_cache_operations_total` | Counter | Redis cache operations by type and result |
| `snflwr_rate_limiter_requests_total` | Counter | Rate limiter checks by tier and result |
| `snflwr_llm_requests_total` | Counter | LLM/Ollama requests by model and result |
| `snflwr_llm_request_duration_seconds` | Histogram | LLM request latency |
| `snflwr_safety_checks_total` | Counter | Safety pipeline checks by layer |
| `snflwr_redis_sentinel_failovers_total` | Counter | Redis Sentinel failover events |

#### Configure Prometheus

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'snflwr-ai'
    static_configs:
      - targets: ['snflwr-api:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

#### Example Grafana Dashboard Queries

```promql
# Request rate per endpoint
rate(snflwr_http_requests_total[5m])

# 95th percentile latency
histogram_quantile(0.95, rate(snflwr_http_request_duration_seconds_bucket[5m]))

# Cache hit rate
sum(rate(snflwr_cache_operations_total{result="hit"}[5m])) /
sum(rate(snflwr_cache_operations_total{operation="get"}[5m]))

# Circuit breaker status (alert on open)
snflwr_circuit_breaker_state{service="ollama"} == 1
```

#### Required Dependency

```bash
pip install prometheus_client
```

### Database Access

```bash
# Connect to PostgreSQL
docker exec -it snflwr-db psql -U snflwr -d snflwr_db

# Example queries
SELECT COUNT(*) FROM parent_accounts;
SELECT * FROM safety_incidents ORDER BY timestamp DESC LIMIT 10;
```

## Maintenance

### Backups

```bash
# Backup database
docker exec snflwr-db pg_dump -U snflwr snflwr_db > backup_$(date +%Y%m%d).sql

# Backup volumes
docker run --rm -v snflwr_postgres-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup.tar.gz -C /data .
```

### Updates

```bash
# Pull latest images
docker-compose -f docker/compose/docker-compose.yml pull

# Restart services
docker-compose -f docker/compose/docker-compose.yml up -d --force-recreate

# Clean old images
docker image prune -a
```

### SSL Certificate Renewal

```bash
# Renew Let's Encrypt (run monthly)
sudo certbot renew --deploy-hook "docker-compose -f docker/compose/docker-compose.yml restart nginx"
```

## Scaling

### Multiple Replicas

```yaml
# In docker-compose.yml
snflwr-api:
  deploy:
    replicas: 3
    resources:
      limits:
        cpus: '2'
        memory: 4G
```

### Separate Ollama Server

For heavy load, run Ollama on a dedicated GPU server:

```yaml
# Change OLLAMA_BASE_URL to remote server
environment:
  - OLLAMA_BASE_URL=http://gpu-server.internal:11434
```

## Troubleshooting

### Ollama Out of Memory

```bash
# Reduce model size or add swap
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Database Connection Errors

```bash
# Check PostgreSQL logs
docker-compose logs postgres

# Verify DATABASE_URL in .env.production
# Ensure password matches
```

### Open WebUI Not Loading

```bash
# Check if middleware is mounted correctly
docker exec snflwr-frontend ls /app/backend/open_webui/middleware

# Restart container
docker-compose restart open-webui
```

## Security Checklist

- [ ] Change all default passwords
- [ ] Enable SSL/TLS
- [ ] Configure firewall (allow only 80, 443)
- [ ] Set up regular backups
- [ ] Enable rate limiting
- [ ] Configure email alerts
- [ ] Set up monitoring (Sentry, etc.)
- [ ] Review Nginx access logs regularly
- [ ] Update Docker images monthly

## Performance Optimization

### Database Tuning

```sql
-- Add in PostgreSQL config
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
```

### Nginx Caching

```nginx
# Add to nginx.conf
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=cache:10m max_size=1g;

location /static/ {
    proxy_cache cache;
    proxy_cache_valid 200 1d;
}
```

## Cost Estimation

**Monthly Costs (Estimated):**

- **VPS (16GB RAM, 4 CPU)**: $40-80/month (Hetzner, DigitalOcean)
- **Domain**: $10-15/year
- **SSL**: Free (Let's Encrypt)
- **Bandwidth**: Usually included
- **Email (SendGrid)**: $15-20/month (for 40k emails)

**Total**: ~$50-100/month for small-medium scale

## Production Checklist

Before going live:

1. [ ] Test all safety pipeline layers
2. [ ] Verify freemium limits work
3. [ ] Test parent dashboard
4. [ ] Configure email alerts
5. [ ] Set up backups
6. [ ] Enable monitoring
7. [ ] Load test with 100+ concurrent users
8. [ ] Document incident response procedures
9. [ ] Train support staff
10. [ ] Prepare legal disclaimers

## Support

For issues or questions:
- GitHub Issues: https://github.com/yourusername/snflwr-ai/issues
- Email: support@snflwr.ai
- Documentation: https://docs.snflwr.ai
