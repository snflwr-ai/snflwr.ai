# Horizontal Scaling Guide
**snflwr.ai - Production Scaling Strategies**

**Last Updated:** 2025-12-25
**Version:** 1.0

---

## Table of Contents

1. [Overview](#overview)
2. [Scaling Tiers](#scaling-tiers)
3. [Component Scaling](#component-scaling)
4. [Load Testing](#load-testing)
5. [Monitoring for Scale](#monitoring-for-scale)
6. [Cost Optimization](#cost-optimization)
7. [Troubleshooting](#troubleshooting)

---

## Overview

snflwr.ai is designed to scale horizontally across multiple dimensions:
- **API servers** (stateless, easy to scale)
- **Celery workers** (background task processing)
- **Ollama instances** (AI inference)
- **Database** (read replicas, connection pooling)
- **Redis** (caching and session storage)

### Scaling Architecture

```
                    Internet
                       │
                       ▼
                Load Balancer
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
    API Server 1   API Server 2   API Server 3
        │              │              │
        └──────────────┼──────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
   PostgreSQL     Redis Cluster   Ollama Cluster
   (Primary +     (3-6 nodes)     (3-10 instances)
    Replicas)
```

---

## Scaling Tiers

### Tier 1: Small (1-1,000 users)

**Infrastructure:**
- 1 API server (4 workers)
- 1 PostgreSQL database
- 1 Redis instance
- 1 Ollama instance
- 2 Celery workers

**Specs:**
- API: 2 CPU, 4GB RAM
- PostgreSQL: 2 CPU, 4GB RAM
- Redis: 1 CPU, 2GB RAM
- Ollama: 4 CPU, 8GB RAM, 1 GPU
- Celery: 1 CPU, 2GB RAM each

**Estimated Cost:** $200-400/month (AWS/GCP)

**Docker Compose:**
```bash
docker-compose -f docker-compose.yml up -d
```

---

### Tier 2: Medium (1,000-10,000 users)

**Infrastructure:**
- 3 API servers (4 workers each)
- 1 PostgreSQL primary + 1 read replica
- 3 Redis nodes (cluster)
- 3 Ollama instances (load balanced)
- 4 Celery workers

**Specs:**
- API: 4 CPU, 8GB RAM each
- PostgreSQL Primary: 4 CPU, 16GB RAM
- PostgreSQL Replica: 4 CPU, 16GB RAM
- Redis: 2 CPU, 4GB RAM each
- Ollama: 8 CPU, 16GB RAM, 1 GPU each
- Celery: 2 CPU, 4GB RAM each

**Estimated Cost:** $1,500-2,500/month

**Deployment:**
```bash
# Kubernetes
kubectl apply -f enterprise/k8s/

# Or Docker Compose with scaling
docker-compose -f docker-compose.yml up -d --scale snflwr-api=3
```

---

### Tier 3: Large (10,000-100,000 users)

**Infrastructure:**
- 10-20 API servers (auto-scaling)
- PostgreSQL cluster (1 primary + 2 replicas)
- Redis cluster (6 nodes, HA)
- 10 Ollama instances (load balanced)
- 10 Celery workers

**Specs:**
- API: 4 CPU, 8GB RAM each (auto-scale)
- PostgreSQL Primary: 8 CPU, 32GB RAM
- PostgreSQL Replicas: 8 CPU, 32GB RAM each
- Redis: 4 CPU, 8GB RAM each
- Ollama: 8 CPU, 16GB RAM, 1 GPU each
- Celery: 4 CPU, 8GB RAM each

**Estimated Cost:** $10,000-20,000/month

**Deployment:**
```bash
# Kubernetes with HPA
kubectl apply -f enterprise/k8s/
```

---

## Component Scaling

### 1. API Servers (Stateless)

**Why Scale:**
- Handle more concurrent requests
- Reduce response time under load
- Provide redundancy

**How to Scale:**

**Docker Compose:**
```bash
# Scale to 5 instances
docker-compose up -d --scale snflwr-api=5
```

**Kubernetes:**
```bash
# Manual scaling
kubectl scale deployment snflwr-api --replicas=5 -n snflwr-ai

# Auto-scaling (HPA already configured)
kubectl get hpa snflwr-api-hpa -n snflwr-ai
```

**When to Scale:**
- CPU usage > 70% sustained
- Response time > 500ms (p95)
- Queue depth > 100 requests

**Metrics to Monitor:**
- `api_request_duration_seconds` (Prometheus)
- `api_requests_total` (Prometheus)
- CPU and memory usage

---

### 2. Celery Workers (Background Tasks)

**Why Scale:**
- Process more background tasks (emails, AI generation, data export)
- Reduce task queue backlog
- Handle spikes in alert volume

**How to Scale:**

**Docker Compose:**
```bash
docker compose -f docker/compose/docker-compose.yml up -d --scale celery-worker=10
```

**Kubernetes:**
```bash
kubectl scale deployment celery-worker --replicas=10 -n snflwr-ai
```

**When to Scale:**
- Task queue depth > 1000
- Task wait time > 30 seconds
- Email delivery delays

**Metrics to Monitor:**
- Celery queue depth (Redis)
- Task processing time
- Worker CPU/memory usage

---

### 3. Ollama Instances (AI Inference)

**Why Scale:**
- Reduce AI response time
- Handle more concurrent inference requests
- Support multiple models simultaneously

**How to Scale:**

**Docker Compose:**
```bash
# Run Ollama cluster
docker-compose -f docker/compose/docker-compose.ollama-cluster.yml up -d
```

**Kubernetes:**
```bash
kubectl scale deployment ollama --replicas=10 -n snflwr-ai
```

**GPU Considerations:**
- Each Ollama instance requires 1 GPU
- Use GPU node pools in Kubernetes
- Consider GPU sharing (MIG on A100)

**When to Scale:**
- AI inference time > 5 seconds (p95)
- GPU utilization > 80%
- Request queue depth > 50

**Metrics to Monitor:**
- `ollama_generate_time` (custom metric)
- GPU utilization (nvidia-smi)
- Request queue depth

---

### 4. PostgreSQL Database

**Scaling Strategies:**

**Vertical Scaling (Single Instance):**
```bash
# Increase instance size
# AWS RDS: Modify instance class (db.m5.large → db.m5.xlarge)
# GCP Cloud SQL: Increase CPU/RAM
```

**Read Replicas (Horizontal Read Scaling):**
```bash
# Create read replica
# Route read-only queries to replica

# Application code:
# Use connection pooling with read/write splitting
```

**Connection Pooling:**
```python
from storage.connection_pool import PostgreSQLConnectionPool

# Increase pool size
pool = PostgreSQLConnectionPool(
    min_connections=10,
    max_connections=50
)
```

**When to Scale:**
- CPU usage > 70%
- Read query latency > 100ms
- Active connections > 80% of max
- Disk I/O saturated

**Metrics to Monitor:**
- `pg_stat_database` (connections, queries)
- `pg_stat_activity` (active queries)
- Disk I/O, CPU, memory

---

### 5. Redis (Caching & Sessions)

**Scaling Strategies:**

**Single Instance (Small Scale):**
```bash
docker run -d redis:7-alpine
```

**Redis Cluster (High Availability):**
```bash
# 6-node cluster (3 masters, 3 replicas)
docker-compose -f docker/compose/docker-compose.redis-cluster.yml up -d
```

**Kubernetes:**
```yaml
# Use Redis Operator or Bitnami Helm Chart
helm install redis bitnami/redis-cluster \
  --set cluster.nodes=6 \
  --namespace snflwr-ai
```

**When to Scale:**
- Memory usage > 80%
- Cache hit rate < 80%
- Eviction rate increasing

**Metrics to Monitor:**
- `redis_memory_used_bytes`
- `redis_keyspace_hits_total`
- `redis_keyspace_misses_total`

---

## Load Testing

### Tools

**1. Apache Bench (ab):**
```bash
# Test API endpoint
ab -n 10000 -c 100 -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/profiles/list
```

**2. Locust (Python-based):**
```python
# locustfile.py
from locust import HttpUser, task, between

class SnflwrUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def list_profiles(self):
        self.client.get("/api/profiles/list", headers={
            "Authorization": f"Bearer {self.token}"
        })

    def on_start(self):
        # Login and get token
        response = self.client.post("/api/auth/login", json={
            "email": "test@example.com",
            "password": "password"
        })
        self.token = response.json()['access_token']

# Run:
locust -f locustfile.py --host=http://localhost:8000
```

**3. k6 (JavaScript-based):**
```javascript
// load-test.js
import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  stages: [
    { duration: '2m', target: 100 },  // Ramp up to 100 users
    { duration: '5m', target: 100 },  // Stay at 100
    { duration: '2m', target: 0 },    // Ramp down
  ],
};

export default function () {
  const response = http.get('http://localhost:8000/api/profiles/list');
  check(response, {
    'status is 200': (r) => r.status === 200,
    'response time < 500ms': (r) => r.timings.duration < 500,
  });
  sleep(1);
}

// Run: k6 run load-test.js
```

### Load Test Scenarios

**1. Baseline (Current Capacity):**
- 100 concurrent users
- 10-minute test
- Measure: p50, p95, p99 response times

**2. Peak Load (2x Baseline):**
- 200 concurrent users
- Identify bottlenecks
- Measure: error rate, response times

**3. Stress Test (Until Failure):**
- Gradually increase load
- Find breaking point
- Identify weakest component

**4. Spike Test:**
- Sudden traffic spike (0 → 500 users in 1 minute)
- Test auto-scaling response
- Measure: recovery time

---

## Monitoring for Scale

### Prometheus Queries

**API Throughput:**
```promql
# Requests per second
rate(api_requests_total[5m])

# 95th percentile response time
histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m]))

# Error rate
rate(api_requests_total{status=~"5.."}[5m]) / rate(api_requests_total[5m])
```

**Database:**
```promql
# Active connections
pg_stat_database_numbackends{datname="snflwr_db"}

# Query duration (p95)
histogram_quantile(0.95, rate(pg_stat_statements_mean_time_bucket[5m]))
```

**Celery:**
```promql
# Queue depth
celery_queue_length{queue="default"}

# Task processing rate
rate(celery_tasks_total{state="SUCCESS"}[5m])
```

### Grafana Dashboards

**Import Pre-built Dashboards:**
- API Performance Dashboard
- Database Metrics Dashboard
- Celery Worker Dashboard
- Ollama Inference Dashboard

(See `docs/MONITORING_GUIDE.md` for dashboard JSON)

---

## Cost Optimization

### 1. Right-Sizing

**Monitor Resource Usage:**
```bash
# CPU and memory usage
kubectl top pods -n snflwr-ai

# Adjust resource requests/limits
kubectl edit deployment snflwr-api -n snflwr-ai
```

**Example:**
```yaml
resources:
  requests:
    memory: "512Mi"  # Start lower
    cpu: "500m"
  limits:
    memory: "1Gi"    # Reduce if unused
    cpu: "1000m"
```

### 2. Auto-Scaling

**Scale Down During Low Traffic:**
```yaml
# HPA min replicas
minReplicas: 2  # Reduce from 3 to 2 during off-hours

# Use cron-based scaling (Kubernetes CronJob)
```

### 3. Spot Instances

**Use Spot/Preemptible VMs (50-90% cost savings):**
```bash
# AWS EKS with Spot instances
eksctl create nodegroup \
  --cluster snflwr-prod \
  --spot \
  --instance-types=m5.xlarge,m5a.xlarge
```

### 4. Database Optimization

**Use Connection Pooling:**
- Reduces database connections
- Improves performance
- Allows smaller database instance

**Read Replicas:**
- Route read-only queries to replicas
- Primary handles writes only

### 5. Caching

**Aggressive Caching (Redis):**
- Profile data (5-minute TTL)
- Safety incidents (1-minute TTL)
- API responses (30-second TTL)

**Expected Savings:** 30-50% reduction in database load

---

## Troubleshooting

### Issue: API Servers Not Scaling

**Symptoms:**
- HPA shows `<unknown>` for metrics
- Replicas not increasing under load

**Solutions:**
```bash
# Check metrics-server is installed
kubectl get deployment metrics-server -n kube-system

# Install if missing
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Verify metrics
kubectl top pods -n snflwr-ai
```

### Issue: Database Connection Pool Exhausted

**Symptoms:**
- `psycopg2.OperationalError: could not connect to server`
- High connection count

**Solutions:**
```python
# Increase pool size
PostgreSQLConnectionPool(max_connections=50)

# Or increase PostgreSQL max_connections
# postgresql.conf: max_connections = 200
```

### Issue: Ollama High Latency

**Symptoms:**
- AI response time > 10 seconds
- GPU utilization low

**Solutions:**
```bash
# Scale Ollama instances
kubectl scale deployment ollama --replicas=10 -n snflwr-ai

# Check GPU availability
kubectl describe node | grep nvidia.com/gpu

# Preload models (reduce cold start)
docker exec ollama-1 ollama pull qwen3.5:9b
```

### Issue: Redis Out of Memory

**Symptoms:**
- `OOM command not allowed when used memory > 'maxmemory'`

**Solutions:**
```bash
# Increase memory limit
docker run -d redis:7-alpine --maxmemory 2gb

# Enable eviction policy
redis-cli config set maxmemory-policy allkeys-lru

# Or scale to Redis cluster
```

---

## Production Checklist

### Before Scaling

- [ ] Load test current infrastructure
- [ ] Identify bottlenecks (CPU, memory, disk, network)
- [ ] Set up monitoring and alerting
- [ ] Document current performance metrics
- [ ] Plan scaling strategy (vertical vs horizontal)

### During Scaling

- [ ] Monitor metrics in real-time
- [ ] Test incrementally (don't jump from 1 → 10 instances)
- [ ] Verify load balancer distribution
- [ ] Check database connection pooling
- [ ] Test failover scenarios

### After Scaling

- [ ] Verify all components healthy
- [ ] Review costs vs performance
- [ ] Update runbooks with new architecture
- [ ] Document scaling decisions
- [ ] Set up alerts for new thresholds

---

## Additional Resources

- [Kubernetes HPA Documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [PostgreSQL High Availability](https://www.postgresql.org/docs/current/high-availability.html)
- [Redis Clustering](https://redis.io/topics/cluster-tutorial)
- [Load Testing Best Practices](https://k6.io/docs/test-types/)

---

**Document Version:** 1.0
**Last Updated:** 2025-12-25
**Next Review:** 2026-03-25 (quarterly)
