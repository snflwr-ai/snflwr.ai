---
---

# snflwr.ai Monitoring Guide

## Overview

This guide covers monitoring, observability, and operational best practices for snflwr.ai in production environments.

**Last Updated:** 2025-12-25
**Production Readiness:** Enhanced monitoring stack implemented

---

## Table of Contents

1. [Monitoring Stack](#monitoring-stack)
2. [Prometheus Metrics](#prometheus-metrics)
3. [Health Checks](#health-checks)
4. [Database Monitoring](#database-monitoring)
5. [Performance Monitoring](#performance-monitoring)
6. [Safety Monitoring](#safety-monitoring)
7. [Alerting](#alerting)
8. [Dashboards](#dashboards)
9. [Troubleshooting](#troubleshooting)

---

## Monitoring Stack

### Components

| Component | Purpose | Endpoint/Location |
|-----------|---------|-------------------|
| **Prometheus** | Metrics collection | `/api/metrics` |
| **Grafana** | Visualization dashboards | Port 3000 (external) |
| **Health Check** | Service availability | `/health`, `/api/health/detailed` |
| **Application Logs** | Event logging | `logs/snflwr.log` |
| **Error Logs** | Error tracking | `logs/errors.log` |
| **Safety Logs** | Child safety incidents | `logs/safety_incidents.log` |
| **Audit Logs** | Security events | Database: `audit_log` table |

### Architecture

```
┌─────────────────┐
│  Snflwr API  │
│  (Port 8000)    │
└────────┬────────┘
         │
         ├─► /api/metrics (Prometheus format)
         ├─► /health (Basic health)
         ├─► /api/health/detailed (Component health)
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│   Prometheus    │────►│   Grafana    │
│  (Port 9090)    │     │  (Port 3000) │
└─────────────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐
│  Alert Manager  │
│  (Notifications)│
└─────────────────┘
```

---

## Prometheus Metrics

### Metrics Endpoint

**URL:** `http://localhost:8000/api/metrics`

**Format:** Prometheus text format (version 0.0.4)

**Access:** Public (no authentication required for monitoring)

### Available Metrics

#### System Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `snflwr_cpu_usage_percent` | gauge | CPU usage percentage |
| `snflwr_cpu_count` | gauge | Number of CPU cores |
| `snflwr_memory_total_bytes` | gauge | Total system memory |
| `snflwr_memory_used_bytes` | gauge | Used system memory |
| `snflwr_memory_usage_percent` | gauge | Memory usage percentage |
| `snflwr_disk_total_bytes` | gauge | Total disk space |
| `snflwr_disk_used_bytes` | gauge | Used disk space |
| `snflwr_disk_usage_percent` | gauge | Disk usage percentage |

#### Application Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `snflwr_uptime_seconds` | counter | Application uptime |
| `snflwr_users_total` | gauge | Total number of users |
| `snflwr_users_active` | gauge | Number of active users |
| `snflwr_profiles_total` | gauge | Total child profiles |
| `snflwr_profiles_active` | gauge | Active child profiles |
| `snflwr_sessions_active` | gauge | Active conversation sessions |
| `snflwr_messages_24h` | gauge | Messages in last 24 hours |

#### Safety Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `snflwr_safety_incidents_24h` | gauge | Safety incidents (24h) | - |
| `snflwr_safety_incidents_by_severity` | gauge | Incidents by severity | `severity` |
| `snflwr_alerts_unacknowledged` | gauge | Unacknowledged parent alerts | - |

#### Performance Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `snflwr_performance_model_response_time_avg_ms` | gauge | Average AI response time |
| `snflwr_performance_model_response_time_max_ms` | gauge | Maximum AI response time |
| `snflwr_performance_safety_filter_time_avg_ms` | gauge | Average safety filter time |
| `snflwr_performance_database_query_time_avg_ms` | gauge | Average database query time |
| `snflwr_performance_api_response_time_avg_ms` | gauge | Average API response time |

### Example: Scrape Metrics

```bash
# Fetch metrics
curl http://localhost:8000/api/metrics

# Example output:
# HELP snflwr_cpu_usage_percent CPU usage percentage
# TYPE snflwr_cpu_usage_percent gauge
snflwr_cpu_usage_percent 23.5

# HELP snflwr_users_active Number of active users
# TYPE snflwr_users_active gauge
snflwr_users_active 142

# HELP snflwr_safety_incidents_by_severity Safety incidents by severity level
# TYPE snflwr_safety_incidents_by_severity gauge
snflwr_safety_incidents_by_severity{severity="minor"} 12
snflwr_safety_incidents_by_severity{severity="major"} 3
snflwr_safety_incidents_by_severity{severity="critical"} 0
```

---

## Health Checks

### Basic Health Check

**Endpoint:** `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-12-25T10:30:00.000Z",
  "database": "postgresql",
  "safety_monitoring": true
}
```

**Use Case:** Load balancer health checks, uptime monitoring

### Detailed Health Check

**Endpoint:** `GET /api/health/detailed`

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-12-25T10:30:00.000Z",
  "components": {
    "database": {
      "status": "healthy",
      "type": "postgresql"
    },
    "system": {
      "status": "healthy",
      "cpu_percent": 23.5,
      "memory_percent": 45.2,
      "disk_percent": 62.1
    },
    "safety_monitoring": {
      "status": "enabled"
    }
  }
}
```

**Use Case:** Detailed system diagnostics, component-level monitoring

### Health Check Monitoring

**With curl:**
```bash
# Basic check
curl http://localhost:8000/health

# Detailed check
curl http://localhost:8000/api/health/detailed

# Check with response time
time curl -s http://localhost:8000/health
```

**With monitoring tools:**
```yaml
# Prometheus blackbox_exporter config
- job_name: 'snflwr-health'
  metrics_path: /probe
  params:
    module: [http_2xx]
  static_configs:
    - targets:
      - http://localhost:8000/health
  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_target
    - target_label: instance
      replacement: snflwr-api
```

---

## Database Monitoring

### Database Performance Metrics

Monitor these key database metrics:

1. **Connection Pool Status** (PostgreSQL)
   - Active connections
   - Idle connections
   - Connection wait time

2. **Query Performance**
   - Average query execution time
   - Slow queries (> 1 second)
   - Query error rate

3. **Database Size**
   - Total database size
   - Table sizes
   - Index sizes

### Query Performance Monitoring

**Enable query logging (PostgreSQL):**
```sql
-- Log slow queries (> 1 second)
ALTER SYSTEM SET log_min_duration_statement = 1000;

-- Reload configuration
SELECT pg_reload_conf();
```

**Analyze slow queries:**
```bash
# View slow queries from PostgreSQL logs
grep "duration:" /var/log/postgresql/postgresql-16-main.log | \
  awk '{print $13, $0}' | sort -rn | head -20
```

### Database Health Checks

```python
# Run from Python
from storage.database import db_manager

# Test database connection
result = db_manager.execute_read("SELECT 1")
print(f"Database healthy: {result[0][0] == 1}")

# Check table counts
tables = db_manager.execute_read(
    "SELECT name FROM sqlite_master WHERE type='table'"
)
print(f"Tables: {len(tables)}")
```

---

## Performance Monitoring

### Application Performance

**Key metrics to track:**

1. **API Response Times**
   - Target: < 2 seconds (95th percentile)
   - Critical: > 5 seconds

2. **AI Generation Times**
   - Budget tier: < 5 seconds
   - Standard tier: < 8 seconds
   - Premium tier: < 15 seconds

3. **Safety Filter Times**
   - Target: < 100ms (keyword filter)
   - Target: < 2 seconds (LLM classifier)

### Performance Logging

**Log performance metrics in code:**
```python
from utils.logger import log_performance_metric
import time

start_time = time.time()
# ... perform operation ...
elapsed_ms = (time.time() - start_time) * 1000

log_performance_metric('api_response_time', elapsed_ms, 'ms')
```

**Query performance statistics:**
```python
from utils.logger import get_performance_statistics

stats = get_performance_statistics('api_response_time')
print(f"Average: {stats['avg']:.2f}ms")
print(f"Max: {stats['max']:.2f}ms")
print(f"Count: {stats['count']}")
```

---

## Safety Monitoring

### Safety Incident Tracking

**Dedicated log file:** `logs/safety_incidents.log`

**Format:** JSON (one incident per line)

**Example:**
```json
{
  "timestamp": "2025-12-25T10:30:00.000Z",
  "type": "prohibited_keyword",
  "profile_id": "profile_abc123",
  "content": "User message snippet...",
  "severity": "major",
  "metadata": {
    "triggered_keywords": ["keyword1", "keyword2"],
    "filter_layer": "keyword"
  }
}
```

### Safety Metrics Dashboard

**Key metrics to monitor:**

1. **Incident Rate**
   - Incidents per hour
   - Incidents per user
   - Trend over time

2. **Severity Distribution**
   - Minor incidents (%)
   - Major incidents (%)
   - Critical incidents (%)

3. **Parent Alert Status**
   - Unacknowledged alerts
   - Alert response time
   - Alert escalation rate

### Safety Alerting

**Alert conditions:**

- **Critical**: Any critical severity incident
- **Major**: > 2 major incidents in 1 hour for same profile
- **Minor**: > 5 minor incidents in 24 hours for same profile

**Alert channels:**
- Email (to parents)
- Admin dashboard
- Optional: Slack/PagerDuty integration

---

## Alerting

### Alert Rules (Prometheus)

Create `alerts.yml` for Prometheus AlertManager:

```yaml
groups:
  - name: snflwr_alerts
    interval: 30s
    rules:
      # System alerts
      - alert: HighCPUUsage
        expr: snflwr_cpu_usage_percent > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage on snflwr.ai"
          description: "CPU usage is {{ $value }}%"

      - alert: HighMemoryUsage
        expr: snflwr_memory_usage_percent > 85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage on snflwr.ai"
          description: "Memory usage is {{ $value }}%"

      - alert: DiskSpaceLow
        expr: snflwr_disk_usage_percent > 80
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Low disk space on snflwr.ai"
          description: "Disk usage is {{ $value }}%"

      # Application alerts
      - alert: NoActiveUsers
        expr: snflwr_users_active == 0
        for: 1h
        labels:
          severity: info
        annotations:
          summary: "No active users"
          description: "No users have been active for 1 hour"

      # Safety alerts
      - alert: HighSafetyIncidents
        expr: snflwr_safety_incidents_24h > 50
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High safety incident rate"
          description: "{{ $value }} incidents in last 24 hours"

      - alert: CriticalSafetyIncidents
        expr: snflwr_safety_incidents_by_severity{severity="critical"} > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Critical safety incidents detected"
          description: "{{ $value }} critical incidents"

      - alert: UnacknowledgedAlerts
        expr: snflwr_alerts_unacknowledged > 10
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "Many unacknowledged parent alerts"
          description: "{{ $value }} alerts need acknowledgment"
```

### AlertManager Configuration

Create `alertmanager.yml`:

```yaml
global:
  smtp_smarthost: 'smtp.sendgrid.net:587'
  smtp_from: 'alerts@snflwr.ai'
  smtp_auth_username: 'apikey'
  smtp_auth_password: '${SENDGRID_API_KEY}'

route:
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'email-ops'

  routes:
    - match:
        severity: critical
      receiver: 'email-ops'
      continue: true

    - match:
        severity: critical
      receiver: 'pagerduty'

receivers:
  - name: 'email-ops'
    email_configs:
      - to: 'ops@snflwr.ai'
        headers:
          Subject: '[snflwr.ai] {{ .GroupLabels.alertname }}'

  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: '${PAGERDUTY_SERVICE_KEY}'
```

---

## Dashboards

### Grafana Dashboard

**Import pre-built dashboard:**

```bash
# Download Grafana dashboard JSON
curl -o snflwr-dashboard.json \
  https://raw.githubusercontent.com/snflwr-ai/grafana-dashboards/main/snflwr-main.json

# Import to Grafana via UI or API
curl -X POST http://localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  -d @snflwr-dashboard.json
```

**Dashboard panels:**

1. **System Overview**
   - CPU usage
   - Memory usage
   - Disk usage
   - Uptime

2. **Application Metrics**
   - Active users
   - Active sessions
   - Message rate
   - Response times

3. **Safety Monitoring**
   - Incident rate
   - Incidents by severity
   - Unacknowledged alerts
   - Alert response time

4. **Performance**
   - API response times (p50, p95, p99)
   - AI generation times
   - Database query times
   - Safety filter times

### Simple Text-Based Dashboard

**View metrics in terminal:**

```bash
#!/bin/bash
# dashboard.sh - Simple monitoring dashboard

while true; do
  clear
  echo "===================================="
  echo "  snflwr.ai Monitoring Dashboard"
  echo "===================================="
  echo ""

  # Fetch metrics
  METRICS=$(curl -s http://localhost:8000/api/metrics)

  # Parse and display
  echo "SYSTEM:"
  echo "  CPU: $(echo "$METRICS" | grep cpu_usage_percent | awk '{print $2}')%"
  echo "  Memory: $(echo "$METRICS" | grep memory_usage_percent | awk '{print $2}')%"
  echo "  Disk: $(echo "$METRICS" | grep disk_usage_percent | awk '{print $2}')%"
  echo ""

  echo "APPLICATION:"
  echo "  Active Users: $(echo "$METRICS" | grep users_active | awk '{print $2}')"
  echo "  Active Profiles: $(echo "$METRICS" | grep profiles_active | awk '{print $2}')"
  echo "  Active Sessions: $(echo "$METRICS" | grep sessions_active | awk '{print $2}')"
  echo ""

  echo "SAFETY (24h):"
  echo "  Total Incidents: $(echo "$METRICS" | grep safety_incidents_24h | awk '{print $2}')"
  echo "  Unack Alerts: $(echo "$METRICS" | grep alerts_unacknowledged | awk '{print $2}')"
  echo ""

  echo "Updated: $(date)"
  sleep 5
done
```

---

## Troubleshooting

### Common Issues

#### 1. Metrics Endpoint Not Responding

**Symptoms:** `/api/metrics` returns 404 or 500

**Solutions:**
```bash
# Check if metrics route is registered
curl http://localhost:8000/docs | grep metrics

# Check logs for errors
tail -f logs/errors.log | grep metrics

# Verify psutil is installed
pip list | grep psutil
```

#### 2. High Memory Usage

**Symptoms:** `snflwr_memory_usage_percent > 85`

**Solutions:**
```bash
# Check process memory
ps aux | grep snflwr | awk '{print $4}'

# Restart with limited workers
API_WORKERS=2 python api/server.py

# Enable garbage collection
python -c "import gc; gc.collect()"
```

#### 3. Database Connection Pool Exhausted

**Symptoms:** Errors about max connections

**Solutions:**
```python
# Check pool status
from storage.connection_pool import get_connection_pool

pool = get_connection_pool()
stats = pool.get_stats()
print(stats)

# Increase pool size in config
POSTGRES_MAX_CONNECTIONS=50  # Increase from 20
```

#### 4. Slow Query Performance

**Symptoms:** High `database_query_time_avg_ms`

**Solutions:**
```bash
# Add performance indexes
python database/add_performance_indexes.py

# Analyze query patterns
sqlite3 database.db "EXPLAIN QUERY PLAN SELECT ..."

# Run ANALYZE to update statistics
python -c "from storage.database import db_manager; db_manager.execute_write('ANALYZE')"
```

---

## Maintenance Tasks

### Daily

- [ ] Check unacknowledged parent alerts
- [ ] Review error logs for anomalies
- [ ] Monitor disk space usage

### Weekly

- [ ] Review safety incident trends
- [ ] Check database backup success
- [ ] Review performance metrics

### Monthly

- [ ] Update alert thresholds based on trends
- [ ] Review and optimize slow queries
- [ ] Capacity planning review
- [ ] Security audit log review

---

## Next Steps

1. **Deploy Prometheus**
   ```bash
   # Pull Prometheus image
   docker pull prom/prometheus

   # Create config file
   cat > prometheus.yml << EOF
   global:
     scrape_interval: 15s
   scrape_configs:
     - job_name: 'snflwr'
       static_configs:
         - targets: ['host.docker.internal:8000']
   EOF

   # Run Prometheus
   docker run -d -p 9090:9090 \
     -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml \
     prom/prometheus
   ```

2. **Deploy Grafana**
   ```bash
   docker run -d -p 3000:3000 grafana/grafana
   ```

3. **Configure Alerting**
   - Set up AlertManager
   - Configure email/Slack notifications
   - Test alert rules

4. **Create Dashboards**
   - Import pre-built dashboards
   - Customize for your needs
   - Share with team

---

**Document Version:** 1.0
**Last Updated:** 2025-12-25
**Maintained By:** snflwr.ai Operations Team
