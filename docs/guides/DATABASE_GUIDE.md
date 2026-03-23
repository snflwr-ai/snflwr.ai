---
---

# Database Configuration Guide

snflwr.ai supports two database backends, each optimized for different deployment scenarios.

---

## SQLite (Default - Privacy & Offline First)

**Best For:**
- Individual families and homeschools
- Privacy-focused deployments
- Offline/USB operation
- K-12 school computer labs
- Zero server administration

**Advantages:**
- ✅ **True Privacy**: All data stored on parent's USB device
- ✅ **Offline First**: No server or network required
- ✅ **Simple Deployment**: Just plug in USB and run
- ✅ **Physical Control**: Parents can see/backup the `.db` file
- ✅ **COPPA/FERPA Compliant**: No cloud data collection
- ✅ **Cross-Platform**: Works on Windows, macOS, Linux

**Configuration:**
```bash
# .env file
DATABASE_TYPE=sqlite
DATABASE_PATH=/path/to/usb/snflwr.db

# Or leave unset - SQLite is the default
```

**USB Deployment:**
```bash
# Database automatically created on USB device
# Parents physically control all data
# Can unplug and take home - complete data portability
```

**Limitations:**
- Single concurrent writer (fine for individual families)
- Limited to ~1-2 TB data (more than enough for K-12 tutoring)
- No advanced PostgreSQL features

---

## PostgreSQL (Enterprise Scale)

**Best For:**
- School districts with IT departments
- Multi-tenant SaaS deployments
- High concurrency requirements
- Cloud hosting platforms
- Advanced analytics needs

**Advantages:**
- ✅ **High Concurrency**: Multiple simultaneous writers
- ✅ **Scalability**: Handles thousands of users
- ✅ **Advanced Features**: JSON, full-text search, CTEs
- ✅ **Cloud Native**: Works with AWS RDS, Google Cloud SQL, etc.
- ✅ **Better Performance**: Optimized for large datasets

**Configuration:**
```bash
# .env file
DATABASE_TYPE=postgresql
POSTGRES_HOST=localhost  # or cloud database hostname
POSTGRES_PORT=5432
POSTGRES_USER=snflwr
POSTGRES_PASSWORD=<secure-password>
POSTGRES_DATABASE=snflwr_ai
```

**Cloud Deployment:**
```bash
# Example: AWS RDS
POSTGRES_HOST=snflwr-db.abc123.us-east-1.rds.amazonaws.com
POSTGRES_PORT=5432
POSTGRES_USER=admin
POSTGRES_PASSWORD=<secure-password>
POSTGRES_DATABASE=snflwr_production
```

**Trade-offs:**
- Requires PostgreSQL server setup/management
- Data stored on server (not on parent's USB device)
- Network connectivity required
- More complex deployment

---

## When to Use Each

### Use SQLite If:
- ✅ Privacy is your #1 priority
- ✅ Parents want physical data control
- ✅ Offline operation is required
- ✅ Simple deployment is important
- ✅ Serving individual families/small groups
- ✅ K-12 school computer labs (isolated USBs)

### Use PostgreSQL If:
- ✅ Serving entire school districts
- ✅ Need multi-tenant SaaS platform
- ✅ High concurrent user load (100+ simultaneous)
- ✅ Cloud hosting required
- ✅ Advanced analytics needed
- ✅ Have IT staff for database management

---

## Migration Between Databases

### SQLite → PostgreSQL

For schools wanting to scale up:

```bash
# 1. Set up PostgreSQL database
createdb snflwr_ai

# 2. Run migration script
python database/migrate_to_postgresql.py

# 3. Update environment variables
DATABASE_TYPE=postgresql
POSTGRES_HOST=localhost
# ... other PostgreSQL settings

# 4. Verify migration
python scripts/verify_migration.py
```

### PostgreSQL → SQLite

For moving to offline/privacy mode:

```bash
# 1. Export PostgreSQL data
pg_dump snflwr_ai > backup.sql

# 2. Switch to SQLite
DATABASE_TYPE=sqlite
DATABASE_PATH=/path/to/usb/snflwr.db

# 3. Import data (custom script required)
# Contact support for data export assistance
```

---

## Performance Characteristics

### SQLite Performance
- **Read Operations**: Excellent (10,000+ queries/sec)
- **Write Operations**: Good (1,000+ inserts/sec)
- **Concurrent Reads**: Unlimited
- **Concurrent Writes**: One at a time (fine for families)
- **Startup Time**: Instant (no server process)

### PostgreSQL Performance
- **Read Operations**: Excellent (multi-core scaling)
- **Write Operations**: Excellent (parallel writes)
- **Concurrent Reads**: Unlimited
- **Concurrent Writes**: Unlimited
- **Startup Time**: Requires server connection

---

## Data Privacy Comparison

### SQLite Privacy Model
```
Parent's USB Device
├── snflwr.db (all data)
├── .encryption_key (master key)
└── logs/ (activity logs)

✅ Parent has complete physical custody
✅ Can unplug and data goes with them
✅ No cloud/server dependency
✅ COPPA/FERPA compliant by design
```

### PostgreSQL Privacy Model
```
Server Database
├── Encrypted data at rest
├── Network encryption (TLS)
└── Access controls

⚠️  Data on school/cloud servers
⚠️  Requires trust in server operator
✅ Still COPPA/FERPA compliant with proper policies
```

---

## Recommendation by Deployment Scenario

| Scenario | Recommended Database | Why |
|----------|---------------------|-----|
| Homeschool family | **SQLite** | Privacy, simplicity, offline |
| Small school (<50 students) | **SQLite** | Easy deployment, cost-effective |
| School district (>100 students) | **PostgreSQL** | Scalability, concurrent access |
| SaaS platform | **PostgreSQL** | Multi-tenancy, cloud native |
| Offline/rural areas | **SQLite** | No network required |
| High-security environments | **SQLite** | Air-gapped operation possible |

---

## Default Configuration

snflwr.ai defaults to **SQLite** because:
1. Privacy by default (parents control data)
2. Simplest deployment (no server setup)
3. Works offline (no network required)
4. Aligns with K-12 safety mission

**You can always switch to PostgreSQL later** if scaling needs change.

---

## Testing Both Databases

The test suite validates both database backends:

```bash
# Test with SQLite (default)
pytest tests/

# Test with PostgreSQL
DATABASE_TYPE=postgresql pytest tests/

# All 378 core tests pass with both backends ✓
```

---

## Support

- **SQLite Questions**: See SQLite documentation at sqlite.org
- **PostgreSQL Questions**: See PostgreSQL docs at postgresql.org
- **Migration Help**: Use migration scripts in `database/` directory
- **Issues**: Report at github.com/snflwr-ai/issues

---

**Bottom Line**: Start with SQLite for simplicity and privacy. Upgrade to PostgreSQL when you need enterprise scale. The abstraction layer makes switching seamless.
