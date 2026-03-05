# snflwr.ai - Production Deployment Guide

Complete guide for deploying snflwr.ai in production environments (schools, homes, or cloud).

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [System Architecture](#system-architecture)
3. [Deployment Options](#deployment-options)
4. [Installation Steps](#installation-steps)
5. [Safety Configuration](#safety-configuration)
6. [User Management](#user-management)
7. [Monitoring & Maintenance](#monitoring--maintenance)
8. [Troubleshooting](#troubleshooting)

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
   cd loving-morse
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
   - Copy/paste code from `openwebui_safety_filter_with_logging.py`
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
cd ~/snflwr-ai/loving-morse
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
5. **Open** `loving-morse/openwebui_safety_filter_with_logging.py`
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
   snflwr-ai:latest           2.0 GB
   llama-guard3:1b               ~1 GB
   ```

2. **If models missing**, create them:
   ```bash
   ollama pull qwen3.5:9b
   ollama pull llama-guard3:1b

   ollama create snflwr-ai:latest -f models/Snflwr_AI_Kids.modelfile
   ```

### Step 5: Configure Model Access

1. **Admin Panel → Workspace → Models**
2. For each model, configure access:

   **Admin-Only Models** (toggle ON "Admin Group access only"):
   - llama-guard3:1b
   - llama3-gradient:8b
   - Base chat model (e.g., `qwen3.5:9b`) -- admins/parents use this directly

   **Student Models** (toggle OFF "Admin Group access only"):
   - snflwr-ai:latest (custom tutor persona)

3. **OR** Use database method (more reliable):
   ```bash
   # Already configured in your setup
   # Students can see: snflwr-ai:latest
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
   cd loving-morse/safety
   python parent_dashboard.py
   ```

2. **Access dashboard**:
   - Open http://localhost:5000
   - View blocked messages
   - See analytics

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
- Limited to snflwr-ai:latest
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
   ollama pull qwen3.5:9b
   ollama pull llama3-gradient:8b
   # Then recreate snflwr models
   ```

### Monthly Maintenance

1. **Update Open WebUI**:
   ```bash
   cd loving-morse/frontend/open-webui
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

---

## Troubleshooting

### Open WebUI won't start

**Problem**: ERR_CONNECTION_REFUSED on http://localhost:3000

**Solutions**:
1. Check Docker Desktop is running
2. Restart services:
   ```bash
   cd loving-morse/frontend/open-webui
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
   - snflwr-ai:latest (3B)
   - Remove premium (8B) if not needed
3. Restart Docker

---

## Production Checklist

Before deploying to students:

- [ ] Docker Desktop installed and running
- [ ] All Snflwr models created (snflwr-ai:latest, llama-guard3:1b)
- [ ] Open WebUI accessible at http://localhost:3000
- [ ] Admin account created
- [ ] Safety filter installed and enabled
- [ ] Safety filter tested with unsafe content (blocks correctly)
- [ ] Student test account created
- [ ] Student can only see snflwr-ai:latest
- [ ] Students group configured
- [ ] Model access permissions verified
- [ ] Backup procedure documented
- [ ] Parent/teacher trained on monitoring (if using)
- [ ] START_SNFLWR.bat tested and working

---

## Support & Resources

**Documentation**:
- Safety Filter Installation: `SAFETY_FILTER_INSTALLATION.md`
- Model Configuration: `MODEL_UPGRADE_SUMMARY.md`
- Admin Settings: `ADMIN_SETUP_GUIDE.md`

**Common Files**:
- Start script: `START_SNFLWR.bat`
- Safety filter: `openwebui_safety_filter_with_logging.py`
- Parent dashboard: `safety/parent_dashboard.py`
- Docker config: `frontend/open-webui/docker-compose.yaml`

**Logs**:
- Open WebUI: `docker logs open-webui`
- Ollama: `docker logs ollama`
- Safety incidents: http://localhost:5000

---

## Next Steps

After deployment:

1. **Monitor first week** - Review safety logs daily
2. **Gather feedback** - Ask students and teachers for input
3. **Adjust filters** - Fine-tune based on real usage
4. **Train users** - Create guides for students/parents
5. **Plan backups** - Set up automated backup schedule

snflwr.ai is ready for production!
