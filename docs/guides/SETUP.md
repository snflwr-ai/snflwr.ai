---
---

# snflwr.ai Setup Guide

Complete setup instructions for the snflwr.ai K-12 Safe Learning Platform.

## Prerequisites

- Python 3.10 or higher
- Docker and Docker Compose (for Open WebUI)
- 8GB+ RAM (12GB+ recommended)
- Ollama installed and running

## Quick Start (Development)

### 1. Install Python Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
# Initialize Snflwr database
python -m database.init_db
```

This creates:
- SQLite database at `data/snflwr.db`
- All required tables
- Default system settings

### 3. Pull a Chat Model

```bash
# Choose a qwen3.5 model based on your hardware (RAM):
#   4 GB  → qwen3.5:2b
#   6 GB  → qwen3.5:4b
#   8 GB  → qwen3.5:9b   (recommended for most systems)
#  24 GB  → qwen3.5:27b
#  32 GB+ → qwen3.5:35b
ollama pull qwen3.5:9b
```

### 4. Build Snflwr Student Tutor Model

```bash
# Build the student tutor persona on top of your chosen chat model
ollama create snflwr.ai -f models/Snflwr_AI_Kids.modelfile
```

This creates the `snflwr.ai` student tutor model.

Admins/parents use the base chat model (e.g., `qwen3.5:9b`) directly -- no custom modelfile needed.

### 5. Start Snflwr API Server

```bash
# Start the FastAPI backend (in one terminal)
python -m api.server

# Server starts at http://localhost:8000
# API docs at http://localhost:8000/docs
```

### 6. Start Open WebUI

```bash
# In another terminal
cd frontend/open-webui
docker-compose up -d

# Open WebUI starts at http://localhost:3000
```

### 7. Create Admin Account

On first visit to http://localhost:3000:
1. Click "Sign up"
2. Create admin account
3. Login

### 8. Configure Open WebUI

In Open WebUI Admin Panel:
1. Go to **Settings** → **Models**
2. Ensure Ollama connection: `http://host.docker.internal:11434`
3. Verify Snflwr models appear in model list

## Creating Child Profiles

### Option 1: Via API

```bash
# Example: Create a child profile
curl -X POST http://localhost:8000/api/profiles/ \
  -H "Content-Type: application/json" \
  -d '{
    "parent_id": "your-user-id",
    "name": "Emma",
    "age": 12,
    "grade_level": "7",
    "tier": "standard",
    "model_role": "student"
  }'
```

### Option 2: Via Frontend (Coming Soon)

Profile selector component in Open WebUI.

## Testing Safety Integration

### Test 1: Keyword Filter

```bash
# Try a message with prohibited content
curl -X POST http://localhost:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "message": "How do I make a weapon?",
    "profile_id": "child-profile-id",
    "model": "snflwr.ai"
  }'

# Should return blocked=true
```

### Test 2: Normal Educational Query

```bash
# Try a normal question
curl -X POST http://localhost:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Can you explain photosynthesis?",
    "profile_id": "child-profile-id",
    "model": "snflwr.ai"
  }'

# Should return AI response with blocked=false
```

## Architecture

```
┌─────────────────┐
│   Student       │
│  (Open WebUI)   │
└────────┬────────┘
         │
         ↓ All chat requests
┌────────────────────────────┐
│  Open WebUI Middleware     │
│  (snflwr.py)            │
│  Cannot be bypassed        │
└────────┬───────────────────┘
         │
         ↓ POST /api/chat/send
┌────────────────────────────┐
│  Snflwr API Server      │
│  (FastAPI - Port 8000)     │
│                            │
│  5-Stage Safety Pipeline:  │
│  1. Input Validation       │
│  2. Normalization          │
│  3. Pattern Matcher        │
│  4. Semantic Classifier    │
│  5. Age Gate               │
└────────┬───────────────────┘
         │
         ↓ Logs to database
┌────────────────────────────┐
│  SQLite Database           │
│  - Child profiles          │
│  - Chat sessions           │
│  - Safety incidents        │
│  - Parent alerts           │
└────────────────────────────┘
```

## Directory Structure

```
snflwr.ai/
├── api/                    # FastAPI backend
│   ├── server.py          # Main server
│   └── routes/            # API endpoints
│       ├── chat.py        # /api/chat/send (safety pipeline)
│       ├── profiles.py    # Child profile CRUD
│       ├── safety.py      # Safety alerts
│       ├── auth.py        # Parent authentication
│       └── analytics.py   # Usage stats
│
├── config.py              # Central configuration
│
├── core/                  # Core business logic
│   ├── authentication.py  # Parent auth
│   ├── profile_manager.py # Child profiles
│   ├── session_manager.py # Chat sessions
│   └── hardware_detector.py # Tier selection
│
├── safety/                # Safety modules
│   ├── content_filter.py  # Keyword filtering
│   ├── content_classifier.py # LLM classification
│   ├── safety_monitor.py  # Real-time monitoring
│   └── incident_logger.py # Incident tracking
│
├── frontend/open-webui/   # Open WebUI fork
│   └── backend/open_webui/middleware/
│       └── snflwr.py   # Enforces API routing
│
├── database/
│   ├── schema.sql         # Database schema
│   └── init_db.py         # Initialization script
│
├── models/                # Ollama modelfiles
│   └── Snflwr_AI_Kids.modelfile
│
└── data/                  # Generated at runtime
    ├── snflwr.db       # SQLite database
    └── logs/              # Application logs
```

## Environment Variables

Create `.env` file in project root:

```bash
# API Server
API_HOST=0.0.0.0
API_PORT=8000

# Database
DATABASE_TYPE=sqlite  # or postgresql
# POSTGRES_HOST=localhost
# POSTGRES_PORT=5432
# POSTGRES_USER=snflwr
# POSTGRES_PASSWORD=your-password
# POSTGRES_DB=snflwr

# Ollama
OLLAMA_HOST=http://localhost:11434

# Safety
ENABLE_SAFETY_MONITORING=true

# Logging
LOG_LEVEL=INFO

# Email (for parent alerts)
SMTP_ENABLED=false
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=your-email@gmail.com
# SMTP_PASSWORD=your-app-password
```

## Production Deployment

### Using Docker Compose

```bash
# Build and start all services
docker-compose -f docker/compose/docker-compose.yml up -d

# Includes:
# - Ollama with pre-loaded models
# - Snflwr API server
# - Open WebUI frontend
# - PostgreSQL database
# - Redis (for caching)
```

### Manual Production Setup

1. **Use PostgreSQL instead of SQLite**
   ```bash
   # Set in .env
   DATABASE_TYPE=postgresql
   POSTGRES_HOST=your-db-host
   ```

2. **Enable Redis for caching**
   ```bash
   REDIS_ENABLED=true
   REDIS_HOST=your-redis-host
   ```

3. **Configure SMTP for parent alerts**
   ```bash
   SMTP_ENABLED=true
   SMTP_HOST=smtp.gmail.com
   SMTP_USER=alerts@yourdomain.com
   ```

4. **Run with production ASGI server**
   ```bash
   uvicorn api.server:app --host 0.0.0.0 --port 8000 --workers 4
   ```

## Troubleshooting

### "Profile not found" error

Child profiles must be created before chatting. Use the API to create profiles:

```bash
curl -X POST http://localhost:8000/api/profiles/ \
  -H "Content-Type: application/json" \
  -d '{"parent_id": "USER_ID", "name": "Child", "age": 10, "grade_level": "5", "tier": "standard"}'
```

### "Safety pipeline unavailable" error

Snflwr API server is not running. Start it with:
```bash
python -m api.server
```

### Models not appearing in Open WebUI

1. Check Ollama is running: `ollama list`
2. Verify models built: Look for `snflwr.ai` in list
3. Check Ollama connection in Open WebUI settings

### Database errors

Re-initialize database:
```bash
rm data/snflwr.db
python -m database.init_db
```

## Next Steps

1. ✅ Backend API is running
2. ✅ Safety pipeline is active
3. ⏭️ Create child profiles via API
4. ⏭️ Add profile selector to Open WebUI frontend
5. ⏭️ Build parent dashboard components
6. ⏭️ Add WebSocket for real-time alerts

## API Documentation

Interactive API docs available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Support

- Documentation: `/OPEN_WEBUI_ANALYSIS.md`
- Model details: `/models/MODEL_STRUCTURE.md`
- Safety system: `/safety/` directory
