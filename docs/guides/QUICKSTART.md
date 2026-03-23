---
---

# snflwr.ai Quick Start Guide

Get up and running with snflwr.ai in under 5 minutes.

---

## One-Command Installation

```bash
python install.py
```

That's it! The interactive installer will guide you through setup.

---

## What the Installer Does

The installer automatically:

1. ✅ Detects your operating system and USB drives
2. ✅ Checks Python version and dependencies
3. ✅ Installs missing packages
4. ✅ Guides you through deployment choice:
   - **Family/USB** (privacy-first, offline)
   - **Enterprise/Server** (scale, cloud)
5. ✅ Generates secure credentials
6. ✅ Creates `.env` configuration file
7. ✅ Sets up database schema
8. ✅ Validates everything works
9. ✅ Shows you exactly what to do next

---

## Installation Scenarios

### Scenario 1: Family/Homeschool Setup (5 minutes)

**You Want:**
- AI tutor for your kids
- Complete data privacy
- No cloud/server setup
- USB drive storage

**Steps:**
```bash
# 1. Plug in USB drive
# 2. Run installer
python install.py

# 3. Choose option 1 (Family/USB)
# 4. Follow prompts (installer detects USB automatically)
# 5. Done! Start the app:
python -m uvicorn api.server:app --reload
```

**Access:**
- Open browser: `http://localhost:8000`
- Parent dashboard: `http://localhost:8000/dashboard`
- Your data: On your USB drive in `SnflwrAI/` folder

---

### Scenario 2: School Deployment (10 minutes)

**You Want:**
- AI tutor for multiple students
- Shared school server
- Centralized database
- Advanced analytics

**Steps:**
```bash
# 1. Set up PostgreSQL database
createdb snflwr_ai

# 2. Run installer
python install.py

# 3. Choose option 2 (Enterprise/Server)
# 4. Enter database connection details
# 5. Installer generates secure credentials
# 6. Done! Start the app:
python -m uvicorn api.server:app --host 0.0.0.0
```

**Access:**
- Server: `http://your-server-ip:8000`
- Multi-user ready
- Scales to 1000+ students

---

## Prerequisites

### Required
- **Python 3.8+** (check: `python --version`)
- **pip** (check: `pip --version`)

### Optional (Installer handles these)
- fastapi
- uvicorn
- argon2-cffi
- redis
- pydantic
- python-dotenv

If packages are missing, the installer will offer to install them automatically.

---

## First Run

### Option 1: Family/USB Mode

```bash
# Start the application
python -m uvicorn api.server:app --reload

# Open your browser
open http://localhost:8000

# Access parent dashboard
# Password is in .env file (PARENT_DASHBOARD_PASSWORD)
```

### Option 2: Enterprise/Server Mode

```bash
# Start the application (accessible from network)
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000

# Access from any device on network
# http://your-server-ip:8000
```

---

## After Installation

### Your Data Location

**Family/USB Mode:**
```
USB Drive/
└── SnflwrAI/
    ├── snflwr.db          # All conversations and profiles
    ├── .encryption_key       # Master encryption key
    └── logs/                 # Application logs
```

**Enterprise Mode:**
```
PostgreSQL Database (on server)
```

### Configuration File

All settings are in `.env`:
```bash
# View configuration
cat .env

# Edit if needed
nano .env  # or your favorite editor
```

**Important:** Keep `.env` secure - it contains secrets!

---

## Common Tasks

### Create First Parent Account

```bash
# Option 1: Via API
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"parent1","password":"SecurePass123","email":"parent@example.com"}'

# Option 2: Via Python script
python scripts/create_admin.py
```

### Add Child Profile

```bash
# Via API (after logging in)
curl -X POST http://localhost:8000/profiles \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"name":"Emma","age":10,"grade":"5th"}'
```

### Access Parent Dashboard

```bash
# Get password from .env
grep PARENT_DASHBOARD_PASSWORD .env

# Open dashboard
open http://localhost:8000/dashboard

# Enter password from .env
```

---

## USB Drive Best Practices

If using Family/USB mode:

### ✅ DO:
- Use a dedicated USB drive for snflwr.ai
- Label the USB drive clearly ("Emma's AI Tutor")
- Keep the USB drive in a safe place
- Back up the USB drive regularly
- Eject the USB drive safely before removing

### ❌ DON'T:
- Share the USB drive between multiple families (data privacy)
- Remove USB drive while app is running (data corruption)
- Use a USB drive with other important files (keep it dedicated)
- Lose the USB drive (contains all data!)

### Backup Your USB Drive

```bash
# Copy entire SnflwrAI folder to backup location
cp -r /Volumes/USB/SnflwrAI ~/Backups/SnflwrAI-backup-2024-01-15

# Or use system backup (Time Machine, File History, etc.)
```

---

## Troubleshooting

### "Permission denied" on install.py
```bash
chmod +x install.py
python install.py
```

### "Module not found" errors
```bash
# Install dependencies manually
pip install -r requirements.txt
```

### "Database connection failed"
```bash
# Check .env file has correct settings
cat .env

# For PostgreSQL, ensure database exists
createdb snflwr_ai
```

### "Port 8000 already in use"
```bash
# Use different port
python -m uvicorn api.server:app --port 8080
```

### USB drive not detected
```bash
# List drives manually and enter path when prompted
# Windows: Check File Explorer for drive letter (E:, F:, etc.)
# macOS: Check /Volumes/
# Linux: Check /media/ or /mnt/
```

---

## Next Steps

1. **Read the documentation:**
   - Database guide: `docs/DATABASE_GUIDE.md`
   - API docs: `http://localhost:8000/docs`

2. **Test the safety features:**
   - Try inappropriate content → should be blocked
   - Check parent dashboard → see safety logs

3. **Create profiles:**
   - Add child profiles with ages/grades
   - Set time limits and restrictions

4. **Explore the API:**
   - Interactive docs at `/docs`
   - Try different endpoints
   - Build custom integrations

---

## Getting Help

- **Documentation:** `/docs` folder
- **API Reference:** `http://localhost:8000/docs`
- **Database Guide:** `docs/DATABASE_GUIDE.md`
- **Issues:** Report bugs on GitHub
- **Security:** Report vulnerabilities privately

---

## Uninstalling

### Family/USB Mode
```bash
# 1. Stop the application
# 2. Eject USB drive
# 3. Delete local files (if any)
rm -rf ~/SnflwrAI
rm .env .env.backup
```

### Enterprise Mode
```bash
# 1. Stop the application
# 2. Drop database
dropdb snflwr_ai

# 3. Remove configuration
rm .env .env.backup
```

---

## Privacy Guarantee

**Family/USB Mode:**
- ✅ ALL data stored on YOUR USB drive
- ✅ NO data sent to cloud/servers
- ✅ Works 100% offline
- ✅ You physically control the data
- ✅ COPPA/FERPA compliant by design

**Enterprise Mode:**
- Data stored on YOUR database server
- Network encryption (TLS)
- Access controls
- Audit logging
- COPPA/FERPA compliant with proper policies

---

## Success Indicators

You'll know installation succeeded when:

1. ✅ Installer shows "Installation Complete!"
2. ✅ `.env` file exists and contains configuration
3. ✅ Database file created (SQLite) or connected (PostgreSQL)
4. ✅ App starts without errors
5. ✅ Browser shows snflwr.ai interface
6. ✅ Parent dashboard accessible with password

---

**Ready to start?**

```bash
python install.py
```

Let the installer guide you through the rest! 🚀
