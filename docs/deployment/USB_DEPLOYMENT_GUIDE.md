---
---

# USB Deployment Guide for snflwr.ai

## Overview

The USB deployment option allows you to run snflwr.ai completely offline from a USB drive, providing maximum privacy and portability. This is perfect for:

- **Families** who want complete data control
- **Homeschools** needing portable, offline operation
- **Privacy-conscious parents** who don't want cloud storage
- **Multiple computers** (move USB between devices seamlessly)

## Building a USB Image

### Using the Automated Builder

```bash
# Build USB image (creates dist/SnflwrAI/)
python scripts/build_usb_image.py

# Build to custom location
python scripts/build_usb_image.py /path/to/output
```

The builder creates:
- **SnflwrAI/** - Ready-to-use USB directory
- **SnflwrAI_USB_YYYYMMDD_HHMMSS.zip** - Distributable package

### What Gets Included

```
SnflwrAI/
├── run_snflwr.bat          # Windows launcher
├── run_snflwr.sh           # macOS/Linux launcher
├── README.md                  # Quick start guide
├── DASHBOARD_PASSWORD.txt     # Generated secure password
├── .env                       # Pre-configured settings
├── app/                       # Application code
│   ├── api/                   # API routes
│   ├── core/                  # Core logic
│   ├── storage/               # Database managers
│   ├── safety/                # Safety filters
│   ├── utils/                 # Utilities
│   ├── models/                # AI model configs
│   ├── config.py              # Configuration
│   ├── requirements.txt       # Python dependencies
│   └── schema.sql             # Database schema
├── data/                      # Your data (IMPORTANT!)
│   └── snflwr.db           # Pre-initialized SQLite database
├── logs/                      # Application logs
├── backups/                   # Database backup location
└── docs/                      # Additional documentation
```

## Deployment Process

### Step 1: Prepare USB Drive

**Minimum Requirements:**
- USB 2.0 or higher
- 1GB available space (500MB app + 500MB for growth)
- FAT32, exFAT, or NTFS format

**Recommended:**
- USB 3.0 for faster performance
- 4GB+ for room to grow
- Durable/shock-resistant drive

### Step 2: Extract to USB

**Option A: Direct Copy**
```bash
# Extract ZIP to USB
unzip SnflwrAI_USB_20251227.zip -d /path/to/usb/

# Verify
ls /path/to/usb/SnflwrAI/
```

**Option B: Manual Build**
```bash
# Build directly to USB
python scripts/build_usb_image.py /path/to/usb/
```

### Step 3: Launch Application

**Windows:**
1. Open USB drive in Explorer
2. Navigate to `SnflwrAI` folder
3. Double-click `run_snflwr.bat`
4. Wait for "Uvicorn running on http://127.0.0.1:8000"
5. Open browser to http://localhost:8000

**macOS:**
1. Open USB drive in Finder
2. Navigate to `SnflwrAI` folder
3. Right-click `run_snflwr.sh` → "Open With" → Terminal
4. If security warning appears, allow execution
5. Open browser to http://localhost:8000

**Linux:**
```bash
cd /media/yourusername/USB_NAME/SnflwrAI
./run_snflwr.sh
```

### Step 4: First-Time Setup

1. **Access Parent Dashboard:**
   - URL: http://localhost:8000/dashboard
   - Password: See `DASHBOARD_PASSWORD.txt` on USB

2. **Create Admin Account:**
   - Follow on-screen prompts
   - Set up parent profile

3. **Add Child Profiles:**
   - Click "Add Child"
   - Enter name, age, grade level
   - Select tier (budget/standard/premium)

4. **Start Learning:**
   - Select child profile
   - Begin safe AI conversations

## Moving Between Computers

### Safe Shutdown

```bash
# In the terminal running the server, press:
Ctrl+C

# Wait for:
"Application shutdown complete."

# Then safely eject USB
```

### Starting on New Computer

1. Plug USB into new computer
2. Run launcher script (same as Step 3 above)
3. Everything works identically!

**Your data follows you:**
- All conversations preserved
- Child profiles intact
- Safety settings maintained
- Learning history retained

## Data Management

### Backing Up Your Database

**Automatic Backup (Recommended):**
```bash
cd /path/to/usb/SnflwrAI
python app/scripts/backup_database.py
```

This creates:
```
backups/snflwr_backup_YYYYMMDD_HHMMSS.db
```

**Manual Backup:**
```bash
# While server is STOPPED
cp data/snflwr.db backups/snflwr_backup_$(date +%Y%m%d).db
```

### Restoring from Backup

```bash
# Stop server (Ctrl+C)
# Copy backup over current database
cp backups/snflwr_backup_20251227_120000.db data/snflwr.db
# Restart server
```

### Disk Space Management

**Check Usage:**
```bash
# Windows
dir SnflwrAI\data

# macOS/Linux
du -sh SnflwrAI/data
```

**Database Growth Estimates:**
- Initial: 336 KB (empty)
- Light use (1 child, 10 sessions): ~2 MB
- Medium use (2 children, 100 sessions): ~10 MB
- Heavy use (5 children, 1000 sessions): ~50 MB

**Cleanup Old Data:**
See `app/utils/data_retention.py` for automated cleanup policies.

## Security Best Practices

### Physical Security

🔒 **The USB drive IS your data. Protect it.**

- Store in a secure location when not in use
- Never leave plugged in unattended public computers
- Consider encryption software for USB drive itself
- Label the USB clearly (but not with sensitive info)

### Password Security

📝 **Dashboard Password:**
- Stored in `DASHBOARD_PASSWORD.txt`
- Grants access to ALL child data
- Can be changed in parent dashboard settings

**To Change Password:**
1. Access dashboard with current password
2. Settings → Security → Change Password
3. Update `DASHBOARD_PASSWORD.txt` manually

### Network Security

🌐 **Internet Connection:**
- Required for AI model access
- NOT required for data storage (stays on USB)
- All data written to USB, not cloud
- Consider firewall rules for extra protection

### Data Encryption

🔐 **Current Encryption:**
- Email addresses: Fernet encrypted
- Database: Stored in plaintext on USB
- Encryption keys: Stored in `data/.encryption_key`

**For Additional Protection:**
- Use USB drive with hardware encryption
- Enable BitLocker (Windows) or FileVault (macOS)
- Store encryption keys separately from USB

## Troubleshooting

### "Python not found"

**Windows:**
1. Install from https://www.python.org/downloads/
2. **IMPORTANT:** Check "Add Python to PATH"
3. Restart computer
4. Try launcher again

**macOS:**
```bash
# Install with Homebrew
brew install python3
```

**Linux:**
```bash
# Debian/Ubuntu
sudo apt install python3 python3-pip

# Fedora/RHEL
sudo dnf install python3 python3-pip
```

### "Port 8000 already in use"

Another application is using port 8000.

**Option 1: Change Port**
Edit launcher script, change:
```bash
--port 8000
```
to:
```bash
--port 8001
```

**Option 2: Kill Existing Process**
```bash
# Find process using port 8000
lsof -i :8000  # macOS/Linux
netstat -ano | findstr :8000  # Windows

# Kill the process
kill <PID>  # macOS/Linux
taskkill /PID <PID> /F  # Windows
```

### "Permission denied" (macOS/Linux)

Make launcher executable:
```bash
chmod +x run_snflwr.sh
```

### Database Errors

**Symptoms:**
- "database is locked"
- "disk I/O error"
- Corrupted data

**Solutions:**

1. **Database Locked:**
```bash
# Stop ALL instances of the server
# Remove lock files
rm data/snflwr.db-shm
rm data/snflwr.db-wal
# Restart server
```

2. **Disk I/O Error:**
- Check USB connection (replug)
- Try different USB port
- Copy to computer, run there, copy back

3. **Corrupted Database:**
```bash
# Restore from backup
cp backups/snflwr_backup_LATEST.db data/snflwr.db
```

### Slow Performance

**USB drive is slow:**
- Use USB 3.0 drive (blue port)
- Try different USB port
- Check Task Manager/Activity Monitor for disk usage

**Too many logs:**
```bash
# Clean old logs
cd logs
rm *.log.1 *.log.2 *.log.3
```

**Database fragmentation:**
```bash
# While server is stopped
sqlite3 data/snflwr.db "VACUUM;"
```

## Advanced Topics

### Multi-USB Setup (Multiple Families)

Each USB is independent:
```
Family1_USB/SnflwrAI/  (Jimmy & Sarah's data)
Family2_USB/SnflwrAI/  (Alex & Morgan's data)
School_USB/SnflwrAI/   (Class data)
```

No cross-contamination - complete isolation.

### Hybrid: USB + Desktop

Want to use both USB and desktop?

**USB for Privacy, Desktop for Performance:**
```bash
# Create desktop instance
python install.py  # Choose local storage

# Keep USB for on-the-go
# Both instances are independent
```

### Migration to PostgreSQL

When you outgrow SQLite:
```bash
# Export from USB SQLite
python app/database/migrate_to_postgresql.py \
  --source data/snflwr.db \
  --dest postgresql://user:pass@host/db

# Your USB data now in PostgreSQL
```

See `docs/DATABASE_GUIDE.md` for full migration guide.

### Automation

**Auto-Backup on Launch:**
Add to launcher script before `uvicorn`:
```bash
python app/scripts/backup_database.py
```

**Scheduled Backups (macOS/Linux):**
```bash
# Create cron job
crontab -e

# Add (runs daily at 2am when USB is plugged in)
0 2 * * * cd /Volumes/USB/SnflwrAI && python app/scripts/backup_database.py
```

## Distribution

### Sharing with Other Families

**Option 1: Give them the ZIP**
```bash
# Send them: SnflwrAI_USB_YYYYMMDD.zip
# They extract to their own USB
# Each gets unique dashboard password
```

**Option 2: Pre-configure USBs**
```bash
# Build multiple copies
for i in {1..5}; do
  python scripts/build_usb_image.py dist/family_$i
done

# Each family gets isolated USB with fresh database
```

### School/Institution Distribution

**For 20+ students:**
- Consider PostgreSQL instead (see DATABASE_GUIDE.md)
- USB still works but management becomes complex
- Each USB is completely independent

**For 5-10 students:**
- USB deployment is perfect
- One USB per family
- Teacher keeps master backup USB

## Privacy & Compliance

### What Data is Stored on USB?

- Parent account (email encrypted)
- Child profiles (names, ages, grades)
- Conversation history
- Safety incident logs
- Usage statistics
- All encrypted data keys

### COPPA Compliance

The USB deployment is **COPPA-compliant** because:
- ✅ Data stored locally (not cloud)
- ✅ Parent controls access (dashboard password)
- ✅ Email addresses encrypted at rest
- ✅ No third-party data sharing
- ✅ Easy data deletion (delete USB)

### FERPA Compliance (Schools)

For educational institutions:
- ✅ Educational records on USB (not vendor servers)
- ✅ Physical possession = access control
- ✅ No external disclosure
- ✅ Parent inspection rights (dashboard access)

### Data Deletion

**Complete Data Removal:**
```bash
# Format the USB drive
# All data permanently deleted
```

**Partial Deletion:**
```bash
# Delete specific child profile via dashboard
# Or manually delete from database
```

## Performance Expectations

### Typical Performance (USB 3.0)

| Operation | Time |
|-----------|------|
| Launch server | 3-5 seconds |
| Load dashboard | <1 second |
| Generate AI response | 2-10 seconds* |
| Save conversation | <100ms |
| Backup database | 1-2 seconds |

*Depends on AI model and internet speed

### Comparison: USB vs Desktop vs PostgreSQL

| Metric | USB | Desktop | PostgreSQL |
|--------|-----|---------|------------|
| Setup Time | 2 minutes | 5 minutes | 30 minutes |
| Portability | ✅ Perfect | ❌ Fixed | ❌ Fixed |
| Privacy | ✅ Physical | ✅ Local | ⚠️ Network |
| Speed | ⚠️ Good | ✅ Great | ✅ Great |
| Scale | 1-5 users | 1-10 users | 100+ users |
| Backup | ✅ Copy file | ✅ Copy file | ⚠️ pg_dump |

## FAQ

**Q: Can I run snflwr.ai without internet?**
A: You can run the server, but AI responses require internet for model access. Data storage works 100% offline.

**Q: What happens if I lose the USB?**
A: All your data is on that USB. Keep regular backups to another location.

**Q: Can I use the same USB on Windows and Mac?**
A: Yes! The USB image works on all platforms.

**Q: How do I upgrade to a new version?**
A: Replace the `app/` folder with new version. Your `data/` folder stays intact.

**Q: Is my data encrypted?**
A: Email addresses are encrypted. Full database encryption requires additional software.

**Q: Can multiple people use the USB at once?**
A: No. Only one computer can run the server at a time from a single USB.

---

## Support

**Issues?**
- Check troubleshooting section above
- Review logs in `logs/` directory
- GitHub issues: https://github.com/yourusername/snflwr-ai/issues

**Need Help?**
- Documentation: See `docs/` folder
- Community: [Your community link]
- Email: [Your support email]

---

*snflwr.ai - Privacy-First K-12 AI Education*
