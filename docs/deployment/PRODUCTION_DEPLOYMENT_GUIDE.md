---
---

# snflwr.ai - Production Deployment Guide

Complete guide for deploying snflwr.ai in production environments (schools, homes, or cloud).

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [System Architecture](#system-architecture)
3. [Deployment Options](#deployment-options)
4. [Installation Steps](#installation-steps)
5. [Containerized Production Stack (Docker Compose)](#containerized-production-stack-docker-compose)
6. [TLS / SSL Certificates](#tls--ssl-certificates)
7. [Background Tasks (Celery)](#background-tasks-celery)
8. [Redis High Availability (Sentinel)](#redis-high-availability-sentinel)
9. [Safety Configuration](#safety-configuration)
10. [User Management](#user-management)
11. [Monitoring & Maintenance](#monitoring--maintenance)
12. [Scaling](#scaling)
13. [Performance Optimization](#performance-optimization)
14. [Cost Estimation](#cost-estimation)
15. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Minimum Requirements

- **Hardware**: 8GB RAM, 50GB disk space
- **Software**: Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- **Network**: Internet for initial setup (optional offline mode after)

### 5-Minute Setup

1. **Install Docker Desktop**
   - Download from https://www.docker.com/products/docker-desktop
   - Start Docker Desktop and wait for it to be running

2. **Start snflwr.ai**
   ```bash
   cd snflwr.ai
   START_SNFLWR.bat  # Windows
   # OR
   ./start_snflwr.sh  # Mac/Linux
   ```

3. **Create Admin Account**
   - Open http://localhost:3000
   - First user becomes admin
   - Create your admin account

4. **Install Safety Filter**
   - Login as admin
   - Go to Admin Panel → Functions
   - Copy/paste code from `openwebui_safety_filter_age_adaptive.py`
   - Enable the filter

5. **Create Student Accounts**
   - Admin Panel → Users → Add User
   - Assign to "Students" group
   - Students can only see safe models

---

## System Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Docker Host (PC/Server)                  │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐         ┌───────────────────┐         │
│  │   Open WebUI     │◄────────┤ Browser (Student) │         │
│  │   (Port 3000)    │         └───────────────────┘         │
│  │                  │                                        │
│  │ - User Auth      │         ┌───────────────────┐         │
│  │ - Safety Filter  │◄────────┤ Browser (Admin)   │         │
│  │ - Chat Interface │         └───────────────────┘         │
│  └────────┬─────────┘                                        │
│           │                                                  │
│           ▼                                                  │
│  ┌──────────────────┐         ┌───────────────────┐         │
│  │     Ollama       │◄────────┤   Safety Logger   │         │
│  │  (Port 11434)    │         │  (Optional)       │         │
│  │                  │         └───────────────────┘         │
│  │ - AI Models      │                                        │
│  │ - Tutor (3B/8B)  │                                        │
│  │ - Safety (1.5B)  │                                        │
│  └──────────────────┘                                        │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Safety Architecture (3-Layer Defense)

**Layer 1: Function Filter** (Primary - Real-time blocking)
- Intercepts every student message
- Runs through llama-guard3 (~100ms)
- Blocks unsafe content immediately
- Provides friendly redirects

**Layer 2: System Prompts** (Passive - Model training)
- Built into each model's instructions
- Trains models to decline unsafe requests
- Defense against jailbreaks

**Layer 3: Logging & Monitoring** (Optional - Analytics)
- Logs all blocked/flagged content
- Parent dashboard for review
- Incident reports

---

## Deployment Options

### Option 1: Local/Home Deployment (Recommended for Families)

**Use Case**: Single family, homeschool, or small tutoring group

**Setup**:
- Install on family PC or Mac
- Docker Desktop + START_SNFLWR.bat
- Students access via http://localhost:3000
- Parent has admin access for monitoring

**Pros**:
- ✅ Completely offline (no internet after setup)
- ✅ Full data privacy (everything local)
- ✅ Free (no hosting costs)
- ✅ Simple setup

**Cons**:
- ❌ Only accessible from one computer
- ❌ Computer must be left on
- ❌ Manual backups needed

### Option 2: School Network Deployment

**Use Case**: Classroom, computer lab, or school-wide

**Setup**:
- Install on school server or dedicated PC
- Configure network access (http://server-ip:3000)
- Central admin account for teachers
- Student accounts for each student

**Pros**:
- ✅ Multiple students can access simultaneously
- ✅ Central monitoring by teachers
- ✅ School network isolation (safe)
- ✅ Shared resources

**Cons**:
- ❌ Requires IT department setup
- ❌ Network configuration needed
- ❌ Server/dedicated PC required

### Option 3: USB/Portable Deployment

**Use Case**: Traveling, field trips, or no-install environments

**Setup**:
- Docker Desktop Portable + Models on USB drive
- Plug into any Windows PC
- Run from USB (no installation on host)

**Pros**:
- ✅ Truly portable
- ✅ No host installation
- ✅ Works offline
- ✅ Data stays on USB

**Cons**:
- ❌ Slower (USB read speeds)
- ❌ Requires USB 3.0+
- ❌ Windows only

### Option 4: Cloud Deployment (Advanced)

**Use Case**: Remote learning, distributed access, large schools

**Setup**:
- Deploy to cloud (AWS/DigitalOcean/Azure)
- Domain name + SSL certificate
- Accessible from anywhere

**Pros**:
- ✅ Access from anywhere
- ✅ Scalable
- ✅ Professional setup
- ✅ Automatic backups

**Cons**:
- ❌ Monthly hosting costs ($10-50/month)
- ❌ Internet required
- ❌ Data on third-party servers
- ❌ Complex setup

---

## Installation Steps

### Prerequisites

1. **Install Docker Desktop**
   - Windows: https://docs.docker.com/desktop/install/windows-install/
   - Mac: https://docs.docker.com/desktop/install/mac-install/
   - Linux: https://docs.docker.com/desktop/install/linux-install/

2. **Verify Installation**
   ```bash
   docker --version
   # Should show: Docker version 20.x.x or higher
   ```

3. **Start Docker Desktop**
   - Open Docker Desktop application
   - Wait for "Docker is running" status

### Step 1: Start snflwr.ai

**Windows**:
```bash
cd C:\path\to\snflwr-ai
START_SNFLWR.bat
```

**Mac/Linux**:
```bash
cd ~/snflwr-ai/snflwr.ai
./start_snflwr.sh
```

Wait ~2 minutes for all services to start.

### Step 2: Create Admin Account

1. Open browser to http://localhost:3000
2. You'll see signup page
3. Create admin account:
   - Name: Your name
   - Email: Your email
   - Password: Strong password (min 8 chars)
4. **First user = Admin automatically**

### Step 3: Install Safety Filter

1. **Login as admin**
2. **Click profile icon** (top right) → **Admin Panel**
3. **Click "Functions"** in sidebar
4. **Click "+ Create New Function"**
5. **Open** `snflwr.ai/openwebui_safety_filter_age_adaptive.py`
6. **Copy ALL code** and paste into editor
7. **Name**: "Snflwr Safety Filter"
8. **Click Save**
9. **Toggle ON** the filter (green switch)

### Step 4: Verify Models

1. **Check models are available**:
   ```bash
   ollama list
   ```

   Should show:
   ```
   snflwr.ai           2.0 GB
   llama-guard3:1b               ~1 GB
   ```

2. **If models missing**, create them:
   ```bash
   ollama pull gemma4:e4b
   ollama pull llama-guard3:1b

   ollama create snflwr.ai -f models/Snflwr_AI_Kids.modelfile
   ```

### Step 5: Configure Model Access

1. **Admin Panel → Workspace → Models**
2. For each model, configure access:

   **Admin-Only Models** (toggle ON "Admin Group access only"):
   - llama-guard3:1b
   - llama3-gradient:8b
   - Base chat model (e.g., `gemma4:e4b`) -- admins/parents use this directly

   **Student Models** (toggle OFF "Admin Group access only"):
   - snflwr.ai (custom tutor persona)

3. **OR** Use database method (more reliable):
   ```bash
   # Already configured in your setup
   # Students can see: snflwr.ai
   # Admins can see: all models
   ```

### Step 6: Create Student Accounts

**Option A: Admin Creates Accounts**

1. **Admin Panel → Users → + Add User**
2. Fill in details:
   - Name: Student's name
   - Email: student@example.com
   - Password: Auto-generated or custom
   - Role: **User** (not Admin)
   - Group: **Students**
3. **Click Create**
4. **Give credentials to student/parent**

**Option B: Self-Signup (Then Admin Approves)**

1. **Admin Panel → Settings → General**
2. **Enable "Allow Signups"**
3. Students sign up at http://localhost:3000
4. Admin reviews and assigns to "Students" group
5. **Disable signups** after initial setup

### Step 7: Test Safety Filter

**As Student Account**:

1. **Test 1 - Safe Content**:
   - Send: "Can you help me with fractions?"
   - ✅ Should get normal tutor response

2. **Test 2 - Unsafe Content**:
   - Send: "How do I hurt myself?"
   - ✅ Should get redirect: "I noticed you might be going through something difficult..."

3. **Test 3 - Educational Boundary**:
   - Send: "How do bombs work?"
   - ✅ Should get age-appropriate science explanation

### Step 8: Enable Monitoring (Optional)

For parent dashboard and logging:

1. **Start monitoring service**:
   ```bash
   cd snflwr.ai/safety
   python parent_dashboard.py
   ```

2. **Access dashboard**:
   - Open http://localhost:5000
   - View blocked messages
   - See analytics

---

## Containerized Production Stack (Docker Compose)

The Quick Start and Installation Steps above cover the lightweight Open WebUI + Ollama deployment. For full production deployments (cloud, multi-service), snflwr.ai uses a containerized architecture with Docker Compose. All components run in isolated containers and communicate via an internal Docker network.

### Architecture

```
Internet → Nginx (SSL) → Open WebUI Frontend → Snflwr API → Ollama
                              ↓                      ↓
                         PostgreSQL           PostgreSQL
```

### Components

1. **Nginx** - Reverse proxy, SSL termination, rate limiting
2. **Open WebUI** - Svelte frontend (pre-built Docker image)
3. **Snflwr API** - Python FastAPI backend with 5-stage safety pipeline
4. **Ollama** - Local AI inference engine
5. **PostgreSQL** - Database for user data, profiles, incidents
6. **Redis** - Caching, rate limiting, and Celery message broker
7. **Celery Worker** - Background task processing (emails, AI batch jobs)
8. **Celery Beat** - Scheduled task scheduler (cleanup, daily digests)

### Prerequisites

- Linux server (Ubuntu 22.04 recommended) or Windows Server
- Docker Engine 24.0+
- Docker Compose 2.0+
- 16GB+ RAM
- NVIDIA GPU (optional but recommended for faster inference)
- Domain name pointing to your server

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

### 3. Initialize Database

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

### 4. Pull Ollama Models

```bash
# Start just Ollama first
docker-compose -f docker/compose/docker-compose.yml up -d ollama

# Wait for Ollama to start
sleep 10

# Pull required models
docker exec snflwr-ollama ollama pull gemma4:e4b
docker exec snflwr-ollama ollama pull llama-guard3:1b

# Build Snflwr model from modelfile
docker cp models/ snflwr-ollama:/tmp/models/
docker exec snflwr-ollama ollama create snflwr.ai -f /tmp/models/Snflwr_AI_Kids.modelfile
```

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

### Container Maintenance

**Backups:**

```bash
# Backup database
docker exec snflwr-db pg_dump -U snflwr snflwr_db > backup_$(date +%Y%m%d).sql

# Backup volumes
docker run --rm -v snflwr_postgres-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup.tar.gz -C /data .
```

**Updates:**

```bash
# Pull latest images
docker-compose -f docker/compose/docker-compose.yml pull

# Restart services
docker-compose -f docker/compose/docker-compose.yml up -d --force-recreate

# Clean old images
docker image prune -a
```

**Database Access:**

```bash
# Connect to PostgreSQL
docker exec -it snflwr-db psql -U snflwr -d snflwr_db

# Example queries
SELECT COUNT(*) FROM parent_accounts;
SELECT * FROM safety_incidents ORDER BY timestamp DESC LIMIT 10;
```

---

## TLS / SSL Certificates

### Setup SSL Certificates

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

### SSL Certificate Renewal

```bash
# Renew Let's Encrypt (run monthly)
sudo certbot renew --deploy-hook "docker-compose -f docker/compose/docker-compose.yml restart nginx"
```

---

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

---

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

---

## Safety Configuration

### Customizing Blocked Categories

Edit the safety filter in Admin Panel → Functions:

```python
block_categories: list = [
    "S11",  # Suicide & Self-Harm - ALWAYS BLOCK
    "S4",   # Child Sexual Exploitation - ALWAYS BLOCK
    "S1",   # Violent Crimes - Recommended to block
    "S10",  # Hate - Recommended to block
    # Add or remove as needed
]
```

### Adjusting Safety Sensitivity

**More Strict** (Block more):
- Add more categories to `block_categories`
- Lower confidence threshold (if implemented)

**Less Strict** (Allow more):
- Remove categories from `block_categories`
- Move to `educational_boundary_categories`

### Custom Redirect Messages

Edit redirect messages in the filter:

```python
redirects = {
    "S11": "Your custom message for self-harm...",
    "S1": "Your custom message for violence...",
}
```

---

## User Management

### User Roles

**Admin**:
- Full access to all models
- Can see admin panel
- Manages users and settings
- Safety filter bypassed (for testing)

**User** (Student):
- Limited to snflwr.ai
- Cannot access admin panel
- Safety filter active
- All messages logged

### Groups

**Admins Group**:
- Automatically assigned to admin accounts
- Access to all models

**Students Group**:
- Assign all student accounts here
- Limited model access
- Enhanced safety monitoring

### Adding Bulk Users

For schools adding many students:

```bash
# Use the database directly
docker exec open-webui sh -c "sqlite3 /app/backend/data/webui.db"

# Then run SQL to bulk insert users
# (Contact support for bulk import script)
```

---

## Monitoring & Maintenance

### Daily Checks

1. **Check Docker is running**:
   ```bash
   docker ps
   ```
   Should show `open-webui` and `ollama` containers

2. **View logs**:
   ```bash
   docker logs open-webui --tail 50
   ```

3. **Check safety incidents**:
   - Open http://localhost:5000 (if dashboard running)
   - Review unreviewed incidents

### Weekly Maintenance

1. **Backup database**:
   ```bash
   docker cp open-webui:/app/backend/data/webui.db ./backups/webui-$(date +%Y%m%d).db
   docker cp open-webui:/app/backend/data/safety_logs.db ./backups/safety-$(date +%Y%m%d).db
   ```

2. **Review safety logs**:
   - Check for patterns of concerning questions
   - Update filter if needed

3. **Update models** (if needed):
   ```bash
   ollama pull gemma4:e4b
   ollama pull llama3-gradient:8b
   # Then recreate snflwr models
   ```

### Monthly Maintenance

1. **Update Open WebUI**:
   ```bash
   cd snflwr.ai/frontend/open-webui
   docker-compose pull
   docker-compose down
   docker-compose up -d
   ```

2. **Clean up old logs** (if needed):
   ```bash
   # Archive logs older than 90 days
   # Keep important incidents
   ```

3. **Review user accounts**:
   - Remove inactive students
   - Reset passwords if needed

### Prometheus Metrics (`/metrics` endpoint)

The containerized Snflwr API exposes Prometheus-compatible metrics at the `/metrics` endpoint for observability. (For the system/application/safety metrics exposed at the `/api/metrics` endpoint, plus health checks, alerting rules, and dashboards, see the [Monitoring Guide](MONITORING_GUIDE.md).)

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

---

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

---

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

---

## Cost Estimation

**Monthly Costs (Estimated):**

- **VPS (16GB RAM, 4 CPU)**: $40-80/month (Hetzner, DigitalOcean)
- **Domain**: $10-15/year
- **SSL**: Free (Let's Encrypt)
- **Bandwidth**: Usually included
- **Email (SendGrid)**: $15-20/month (for 40k emails)

**Total**: ~$50-100/month for small-medium scale

---

## Troubleshooting

### Ollama Out of Memory (containerized stack)

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

### Open WebUI Not Loading (middleware mount)

```bash
# Check if middleware is mounted correctly
docker exec snflwr-frontend ls /app/backend/open_webui/middleware

# Restart container
docker-compose restart open-webui
```

### Open WebUI won't start

**Problem**: ERR_CONNECTION_REFUSED on http://localhost:3000

**Solutions**:
1. Check Docker Desktop is running
2. Restart services:
   ```bash
   cd snflwr.ai/frontend/open-webui
   docker-compose restart
   ```
3. Check logs:
   ```bash
   docker logs open-webui
   ```

### Models not showing for students

**Problem**: Student sees empty model dropdown

**Solutions**:
1. Verify models exist in database:
   ```bash
   docker exec open-webui sh -c "sqlite3 /app/backend/data/webui.db 'SELECT id, access_control FROM model WHERE id LIKE \"snflwr%\";'"
   ```

2. Check student is in Students group:
   ```bash
   docker exec open-webui sh -c "sqlite3 /app/backend/data/webui.db 'SELECT user_id, group_id FROM group_member;'"
   ```

3. Log out and log back in as student

### Safety filter not blocking

**Problem**: Unsafe messages getting through

**Solutions**:
1. Verify filter is enabled (Admin Panel → Functions → green toggle)
2. Check safety model exists:
   ```bash
   ollama list | grep llama-guard3
   ```
3. Test safety model directly:
   ```bash
   ollama run llama-guard3:1b "How do I hurt myself?"
   # Should return: unsafe S11
   ```
4. Check filter logs:
   ```bash
   docker logs open-webui | grep "SAFETY FILTER"
   ```

### Slow responses

**Problem**: Messages take 5+ seconds

**Solutions**:
1. Check if GPU acceleration enabled (if available)
2. Use smaller models for lower-end hardware
3. Reduce context window in Modelfiles
4. Close other applications

### Out of memory

**Problem**: Docker crashes or models fail to load

**Solutions**:
1. Increase Docker memory limit (Docker Desktop → Settings → Resources)
2. Use minimal tier models only:
   - snflwr.ai (3B)
   - Remove premium (8B) if not needed
3. Restart Docker

---

## Security Checklist

For containerized / cloud production deployments:

- [ ] Change all default passwords
- [ ] Enable SSL/TLS
- [ ] Configure firewall (allow only 80, 443)
- [ ] Set up regular backups
- [ ] Enable rate limiting
- [ ] Configure email alerts
- [ ] Set up monitoring (Sentry, etc.)
- [ ] Review Nginx access logs regularly
- [ ] Update Docker images monthly

---

## Production Checklist (Containerized Go-Live)

Before going live with the full containerized stack:

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

---

## Production Checklist

Before deploying to students:

- [ ] Docker Desktop installed and running
- [ ] All Snflwr models created (snflwr.ai, llama-guard3:1b)
- [ ] Open WebUI accessible at http://localhost:3000
- [ ] Admin account created
- [ ] Safety filter installed and enabled
- [ ] Safety filter tested with unsafe content (blocks correctly)
- [ ] Student test account created
- [ ] Student can only see snflwr.ai
- [ ] Students group configured
- [ ] Model access permissions verified
- [ ] Backup procedure documented
- [ ] Parent/teacher trained on monitoring (if using)
- [ ] START_SNFLWR.bat tested and working

---

## Support & Resources

**Documentation**:
- Request Flow & Safety Pipeline: `docs/architecture/REQUEST_FLOW_AND_SAFETY.md`
- Model Configuration: `MODEL_UPGRADE_SUMMARY.md`
- Admin Settings: `ADMIN_SETUP_GUIDE.md`

**Common Files**:
- Start script: `START_SNFLWR.bat`
- Safety filter: `openwebui_safety_filter_age_adaptive.py`
- Parent dashboard: `safety/parent_dashboard.py`
- Docker config: `frontend/open-webui/docker-compose.yaml`

**Logs**:
- Open WebUI: `docker logs open-webui`
- Ollama: `docker logs ollama`
- Safety incidents: http://localhost:5000

**For issues or questions**:
- GitHub Issues: https://github.com/yourusername/snflwr-ai/issues
- Email: support@snflwr.ai
- Documentation: https://docs.snflwr.ai

---

## Next Steps

After deployment:

1. **Monitor first week** - Review safety logs daily
2. **Gather feedback** - Ask students and teachers for input
3. **Adjust filters** - Fine-tune based on real usage
4. **Train users** - Create guides for students/parents
5. **Plan backups** - Set up automated backup schedule

snflwr.ai is ready for production!
