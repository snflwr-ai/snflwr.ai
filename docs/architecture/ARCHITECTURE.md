---
---

# snflwr.ai Architecture

## System Overview

snflwr.ai is built as a layered, modular Python application designed for both offline USB deployment and enterprise cloud deployment.

```
┌─────────────────────────────────────────────────────────────┐
│                     Presentation Layer                       │
│  ┌──────────────────┐        ┌──────────────────────────┐  │
│  │  FastAPI REST    │        │  Gradio Web UI           │  │
│  │  API Server      │        │  (Parent Dashboard)      │  │
│  └──────────────────┘        └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                      API Layer (api/)                        │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Authentication  │  │  Parent Routes   │               │
│  │  Endpoints       │  │  (Dashboard)     │               │
│  └──────────────────┘  └──────────────────┘               │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Child Routes    │  │  Admin Routes    │               │
│  │  (Chat)          │  │  (Management)    │               │
│  └──────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Business Logic Layer (core/)               │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │ Authentication   │  │  Child Profile   │               │
│  │ Manager          │  │  Manager         │               │
│  └──────────────────┘  └──────────────────┘               │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Conversation    │  │  Parent Alert    │               │
│  │  Manager         │  │  Manager         │               │
│  └──────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Safety Layer (safety/)                     │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Content Filter  │  │  Behavior        │               │
│  │  (Keyword-Based) │  │  Monitor         │               │
│  └──────────────────┘  └──────────────────┘               │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Usage Quota     │  │  Incident        │               │
│  │  Manager         │  │  Recorder        │               │
│  └──────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                 Data Access Layer (storage/)                 │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Database        │  │  Encryption      │               │
│  │  Manager         │  │  Manager         │               │
│  │  (SQLite)        │  │  (Argon2+NaCl)   │               │
│  └──────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Infrastructure Layer (utils/)              │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │  Ollama Client   │  │  Rate Limiter    │               │
│  │  (LLM Provider)  │  │                  │               │
│  └──────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow Diagrams

### Authentication Flow

```
┌──────────┐       ┌──────────────┐       ┌──────────────┐
│  Client  │──1──>│ Authentication│──2──>│   Database   │
│          │       │   Manager     │       │   Manager    │
└──────────┘       └──────────────┘       └──────────────┘
     │                    │                       │
     │<──────────5────────│<──────────4───────────│
     │                    │                       │
     │                    3. Generate JWT         │
     │                    │                       │
     v                    v                       v
  Returns JWT      Validates creds      Returns user data

1. POST /auth/login {username, password}
2. Query user by username
3. Verify Argon2 password hash
4. Return user record
5. Issue signed JWT token (24hr expiry)
```

### Child Chat Safety Flow

```
┌──────────┐       ┌──────────────┐       ┌──────────────┐
│  Child   │──1──>│ Conversation │──2──>│   Content    │
│  Client  │       │   Manager    │       │   Filter     │
└──────────┘       └──────────────┘       └──────────────┘
     │                    │                       │
     │                    │<─────3: Safe──────────│
     │                    │                       │
     │                    4. Send to LLM          │
     │                    │                       │
     │                    v                       │
     │             ┌──────────────┐               │
     │             │    Ollama    │               │
     │             │   LLM Model  │               │
     │             └──────────────┘               │
     │                    │                       │
     │                    5. Get response         │
     │                    │                       │
     │                    v                       │
     │             ┌──────────────┐               │
     │             │   Content    │<──────────────┘
     │             │   Filter     │
     │             └──────────────┘
     │                    │
     │<──────7: Safe response────│
     │                    │
     │                    6. Check response
     │                    │
     │                    v
     │             ┌──────────────┐
     │             │   Database   │
     │             │  (Log msg)   │
     │             └──────────────┘

If unsafe detected at step 3 or 6:
  → Create safety incident
  → Notify parent
  → Return safe error message
```

### Parent Oversight Flow

```
┌──────────┐       ┌──────────────┐       ┌──────────────┐
│  Parent  │──1──>│ Parent Alert │──2──>│   Database   │
│Dashboard │       │   Manager    │       │   Manager    │
└──────────┘       └──────────────┘       └──────────────┘
     │                    │                       │
     │                    │<─────3: Incidents─────│
     │<──────4: Display───│                       │
     │                    │                       │
     │──────5: Review─────>│                       │
     │                    │──────6: Update────────>│
     │<─────7: Confirm────│<──────────────────────│

Features:
- View all child conversations
- Review flagged content
- Acknowledge/dismiss alerts
- Adjust safety settings
- View usage statistics
```

## Core Components

### Authentication Manager (core/authentication.py)

**Responsibilities:**
- User registration and login
- Password hashing (Argon2)
- JWT token generation and validation
- Session management
- Role-based access control (parent/child/admin)

**Key Methods:**
- `create_parent_account(username, password, email)` - Register new parent
- `authenticate_parent(username, password)` - Login and get JWT
- `verify_token(token)` - Validate JWT and extract claims
- `create_child_profile(parent_id, name, age, ...)` - Add child profile

### Conversation Manager (core/conversation.py)

**Responsibilities:**
- Chat session lifecycle
- Message routing and storage
- Safety filter integration
- Context management for LLM
- Conversation history retrieval

**Key Methods:**
- `start_session(child_id)` - Create new chat session
- `send_message(session_id, user_msg)` - Process user input → LLM → response
- `get_conversation_history(session_id)` - Retrieve chat log

### Safety Pipeline (safety/pipeline.py)

**5-Stage Fail-Closed Pipeline:**
1. **Input Validation** - Length, encoding, injection detection
2. **Normalization** - Unicode normalization, whitespace cleanup, obfuscation removal
3. **Pattern Matching** - Keyword-based blocking with compiled regex patterns
4. **Semantic Classification** - LLM-based safety classification (context-aware)
5. **Age Gate** - Grade-level content enforcement (K-5, 6-8, 9-12)

**Categories:**
- Violence & weapons
- Explicit content
- Cyberbullying & harassment
- Personal information (PII)
- Dangerous activities
- Hate speech
- Manipulation attempts

**Key Methods:**
- `safety_pipeline.check_input(text, age, profile_id)` - Full 5-stage check on user input
- `safety_pipeline.check_output(text, age, profile_id)` - Output validation (stages 3 + 5)
- `safety_pipeline.get_safe_response(result)` - Generate age-appropriate redirect

### Database Manager (storage/database.py)

**Responsibilities:**
- SQLite connection pooling
- CRUD operations
- Transaction management
- Schema migrations
- Query parameterization (SQL injection prevention)

**Tables:**
- `users` - Parent accounts
- `child_profiles` - Child accounts
- `conversation_sessions` - Chat sessions
- `messages` - Individual messages
- `safety_incidents` - Flagged content
- `parent_alerts` - Notifications
- `usage_quotas` - Rate limiting data
- `audit_log` - Security events
- `auth_tokens` - Active JWT tokens

### Encryption Manager (storage/encryption.py)

**Responsibilities:**
- Field-level encryption (emails, PII)
- Password hashing (Argon2)
- Key derivation
- Secure random generation

**Algorithms:**
- Argon2id for password hashing
- NaCl (libsodium) for symmetric encryption
- PBKDF2 for key derivation

## Database Schema

```sql
-- Parent accounts
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email_hash TEXT UNIQUE,
    email_encrypted BLOB,
    role TEXT DEFAULT 'parent',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Child profiles
CREATE TABLE child_profiles (
    id INTEGER PRIMARY KEY,
    parent_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    age INTEGER NOT NULL,
    safety_level TEXT DEFAULT 'strict',
    daily_time_limit INTEGER DEFAULT 3600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chat sessions
CREATE TABLE conversation_sessions (
    id INTEGER PRIMARY KEY,
    child_id INTEGER REFERENCES child_profiles(id),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    status TEXT DEFAULT 'active'
);

-- Individual messages
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES conversation_sessions(id),
    role TEXT NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    flagged BOOLEAN DEFAULT FALSE
);

-- Safety violations
CREATE TABLE safety_incidents (
    id INTEGER PRIMARY KEY,
    child_id INTEGER REFERENCES child_profiles(id),
    session_id INTEGER REFERENCES conversation_sessions(id),
    message_id INTEGER REFERENCES messages(id),
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    details TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE
);

-- Parent notifications
CREATE TABLE parent_alerts (
    id INTEGER PRIMARY KEY,
    parent_id INTEGER REFERENCES users(id),
    child_id INTEGER REFERENCES child_profiles(id),
    incident_id INTEGER REFERENCES safety_incidents(id),
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Security Architecture

### Defense in Depth

**Layer 1: Network Security**
- HTTPS/TLS encryption (production)
- CORS policy enforcement
- Rate limiting (100 req/min per IP)
- Request size limits

**Layer 2: Authentication & Authorization**
- JWT-based authentication (24hr expiry)
- Argon2id password hashing
- Role-based access control
- Token blacklisting on logout

**Layer 3: Input Validation**
- Pydantic schema validation
- SQL injection prevention (parameterized queries)
- XSS prevention (output encoding)
- Path traversal prevention

**Layer 4: Data Protection**
- Field-level encryption (emails, PII)
- Encrypted at rest (SQLite encryption extension available)
- Secure key management
- No plaintext storage of sensitive data

**Layer 5: Content Safety**
- Pre-LLM content filtering
- Post-LLM response filtering
- Behavioral monitoring
- Audit logging

**Layer 6: Privacy Compliance**
- COPPA compliance (parental consent, minimal data collection)
- FERPA compliance (education records protection)
- Data minimization principle
- Right to deletion support

## Deployment Models

### USB Deployment (Offline)

**Architecture:**
- Portable SQLite database on USB drive
- Ollama LLM runs locally
- No internet connection required
- Single-user or family use

**Components:**
- Portable Python environment
- Pre-downloaded Ollama model
- Launcher script (launcher.sh)
- USB image builder (create_usb_image.sh)

**Setup:**
```bash
./create_usb_image.sh /dev/sdX  # Create bootable USB
./launcher.sh                    # Start all services
```

### Enterprise Deployment (Cloud)

**Architecture:**
- PostgreSQL database (multi-tenant)
- Distributed Ollama instances
- Load balancing (nginx)
- Container orchestration (Docker Compose/K8s)

**Components:**
- Dockerfile (API server)
- Reverse proxy (nginx)
- Monitoring (Prometheus + Grafana)
- Log aggregation

**Setup:**
```bash
docker-compose up -d  # Start all services
```

## Performance Optimizations

### Database
- Connection pooling (sqlite3 built-in)
- Indexed queries (user lookups, session retrieval)
- Batch inserts for messages
- Periodic VACUUM for optimization

### LLM Inference
- Streaming responses (Generator pattern)
- Context window management (last 10 messages)
- Model caching (Ollama keeps model in RAM)
- Optional GPU acceleration

### Caching
- In-memory conversation context
- Cached safety filter patterns
- Rate limiter state (in-memory)

### API
- Async endpoints (FastAPI)
- Request batching
- Response compression (gzip)

## Monitoring & Observability

### Metrics Tracked
- Request latency (p50, p95, p99)
- Error rates by endpoint
- Safety incident frequency
- Active sessions count
- Database query performance
- LLM response time
- Token usage per session

### Logging
- Structured JSON logging
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Audit trail for security events
- PII redaction in logs

### Alerting (Enterprise)
- Failed authentication attempts (5+ in 5 min)
- Database connection failures
- Disk space warnings
- High safety incident rate

## Technology Stack

### Backend
- **Language:** Python 3.11+
- **Web Framework:** FastAPI 0.100+
- **Database:** SQLite 3 (USB) / PostgreSQL (Enterprise)
- **ORM:** Raw SQL with parameterized queries
- **Authentication:** JWT (PyJWT)
- **Encryption:** Argon2-cffi, PyNaCl

### AI/ML
- **LLM Provider:** Ollama
- **Chat Model:** Qwen3 family (size chosen by hardware detection, offline-capable)
- **Inference:** HTTP API client

### Frontend
- **Framework:** Gradio 4.0+ (Parent Dashboard)
- **API Client:** FastAPI auto-generated OpenAPI

### DevOps
- **Containerization:** Docker
- **CI/CD:** GitHub Actions
- **Testing:** pytest, pytest-cov
- **Code Quality:** Black, Pylint, MyPy, Bandit
- **Security Scanning:** Safety, Trivy, CodeQL, Gitleaks

### Deployment
- **USB Launcher:** Bash scripts
- **Process Manager:** Subprocess module
- **Reverse Proxy:** nginx (optional)

## Scaling Considerations

### Horizontal Scaling
- Stateless API servers (JWT auth)
- Shared database (PostgreSQL)
- Load balancer (nginx/HAProxy)
- Session affinity not required

### Vertical Scaling
- Ollama benefits from GPU (CUDA/ROCm)
- RAM requirements: 8GB+ (model + app)
- CPU: 4+ cores recommended

### Database Scaling
- Read replicas for analytics
- Partitioning by tenant (enterprise)
- Archival strategy for old conversations

## Development Workflow

1. **Local Development**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python database/init_db.py
   python run_tests.py
   ```

2. **Testing**
   ```bash
   pytest tests/ --cov  # 378/378 tests passing
   ```

3. **Code Quality**
   ```bash
   black .
   pylint api/ core/ safety/ storage/ utils/
   mypy .
   bandit -r .
   ```

4. **CI/CD Pipeline**
   - Code quality checks (Black, Pylint, MyPy)
   - Security scans (Bandit, Safety)
   - Unit tests (pytest)
   - Integration tests
   - Docker build
   - Deployment (manual approval)

## Future Architecture Enhancements

### Planned Improvements
- Multi-tenant database isolation
- Redis caching layer
- Webhook support for parent alerts
- Mobile app (React Native)
- Voice interface (Whisper STT)
- Multi-language support (i18n)
- Advanced analytics dashboard

### Research Areas
- LLM-based content moderation (in addition to keywords)
- Federated learning for personalized safety
- Blockchain-based audit trail (immutable logs)
