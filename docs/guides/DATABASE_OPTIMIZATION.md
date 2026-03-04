# Database Optimization Guide
**snflwr.ai - Production Performance Tuning**

**Last Updated:** 2025-12-25
**Version:** 1.0

---

## Table of Contents

1. [Overview](#overview)
2. [Index Optimization](#index-optimization)
3. [Query Optimization](#query-optimization)
4. [Connection Pooling](#connection-pooling)
5. [PostgreSQL-Specific Tuning](#postgresql-specific-tuning)
6. [SQLite-Specific Tuning](#sqlite-specific-tuning)
7. [Monitoring & Analysis](#monitoring--analysis)
8. [Best Practices](#best-practices)

---

## Overview

This guide provides database optimization strategies for snflwr.ai to achieve optimal performance in production environments.

### Performance Goals

| Metric | Target | Critical Threshold |
|--------|--------|-------------------|
| Query response time (95th percentile) | < 100ms | < 500ms |
| API endpoint latency | < 200ms | < 1s |
| Database connections | 10-20 | 50 max |
| Index hit rate | > 95% | > 85% |
| Cache hit rate | > 80% | > 60% |

---

## Index Optimization

### Existing Indexes

All production-ready indexes have been created in `database/add_performance_indexes.py`. Run this script to apply them:

```bash
python database/add_performance_indexes.py
```

### Index Strategy

**Composite Indexes for Common Queries:**

```sql
-- User authentication (login)
CREATE INDEX idx_users_email_active ON users(email, is_active);

-- Session lookup (JWT validation)
CREATE INDEX idx_sessions_token_active ON auth_sessions(session_token, is_active);

-- Profile listing (parent dashboard)
CREATE INDEX idx_profiles_parent_active ON child_profiles(parent_id, is_active);

-- Message retrieval (conversation history)
CREATE INDEX idx_messages_session_time ON messages(session_id, timestamp DESC);

-- Safety incident lookup (parent alerts)
CREATE INDEX idx_incidents_profile_time ON safety_incidents(profile_id, timestamp DESC);
CREATE INDEX idx_incidents_severity_ack ON safety_incidents(severity, acknowledged);
```

### Index Monitoring

**Check index usage (PostgreSQL):**

```sql
-- Find unused indexes
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan as scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched,
    pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes
WHERE idx_scan = 0
  AND schemaname = 'public'
ORDER BY pg_relation_size(indexrelid) DESC;

-- Index hit rate (should be > 95%)
SELECT
    sum(idx_blks_hit) / nullif(sum(idx_blks_hit + idx_blks_read), 0) * 100 AS index_hit_rate
FROM pg_statio_user_indexes;
```

**SQLite index analysis:**

```sql
-- Analyze query plan
EXPLAIN QUERY PLAN
SELECT * FROM messages
WHERE session_id = 'abc123'
ORDER BY timestamp DESC
LIMIT 20;

-- Should show: "USING INDEX idx_messages_session_time"
```

---

## Query Optimization

### Common Query Patterns

#### 1. **Profile Listing (Parent Dashboard)**

**Inefficient:**
```python
# N+1 query problem
profiles = db.execute_read("SELECT * FROM child_profiles WHERE parent_id = ?", (user_id,))
for profile in profiles:
    # Separate query for each profile
    incidents = db.execute_read("SELECT COUNT(*) FROM safety_incidents WHERE profile_id = ?", (profile['profile_id'],))
```

**Optimized:**
```python
# Single query with JOIN
query = """
SELECT
    cp.*,
    COUNT(si.incident_id) as incident_count,
    SUM(CASE WHEN si.acknowledged = 0 THEN 1 ELSE 0 END) as unacked_incidents
FROM child_profiles cp
LEFT JOIN safety_incidents si ON cp.profile_id = si.profile_id
WHERE cp.parent_id = ?
  AND cp.is_active = 1
GROUP BY cp.profile_id
ORDER BY cp.created_at DESC
"""
profiles = db.execute_read(query, (user_id,))
```

#### 2. **Message Retrieval (Paginated)**

**Inefficient:**
```python
# Fetches all messages, filters in Python
messages = db.execute_read("SELECT * FROM messages WHERE session_id = ?", (session_id,))
paginated = messages[offset:offset+limit]  # Wasteful
```

**Optimized:**
```python
# Database-level pagination with covering index
query = """
SELECT message_id, role, content, timestamp
FROM messages
WHERE session_id = ?
ORDER BY timestamp DESC
LIMIT ? OFFSET ?
"""
messages = db.execute_read(query, (session_id, limit, offset))
```

#### 3. **Safety Incident Search**

**Inefficient:**
```python
# Full table scan
query = """
SELECT * FROM safety_incidents
WHERE timestamp > ?
  AND profile_id IN (SELECT profile_id FROM child_profiles WHERE parent_id = ?)
"""
```

**Optimized:**
```python
# Use JOIN instead of subquery, leverage indexes
query = """
SELECT si.*
FROM safety_incidents si
INNER JOIN child_profiles cp ON si.profile_id = cp.profile_id
WHERE cp.parent_id = ?
  AND si.timestamp > ?
  AND si.acknowledged = 0
ORDER BY si.timestamp DESC
LIMIT 100
"""
```

### Query Analysis Tools

**PostgreSQL EXPLAIN ANALYZE:**

```sql
EXPLAIN ANALYZE
SELECT * FROM messages
WHERE session_id = 'abc123'
ORDER BY timestamp DESC
LIMIT 20;

-- Look for:
-- - Index Scan (good) vs Seq Scan (bad)
-- - Actual time vs Planned time
-- - Rows returned vs Rows scanned
```

**Sample output:**
```
Index Scan using idx_messages_session_time on messages
  (cost=0.42..12.45 rows=20 width=256)
  (actual time=0.023..0.041 rows=20 loops=1)
  Index Cond: (session_id = 'abc123')
Planning Time: 0.102 ms
Execution Time: 0.065 ms
```

✅ **Good indicators:**
- Index Scan (not Seq Scan)
- Execution time < 1ms
- Rows returned ≈ Rows scanned

❌ **Bad indicators:**
- Seq Scan on large tables
- Execution time > 100ms
- Rows scanned >> Rows returned

---

## Connection Pooling

### PostgreSQL Connection Pool

**Configuration** (`storage/connection_pool.py`):

```python
from storage.connection_pool import PostgreSQLConnectionPool

# Initialize pool
pool = PostgreSQLConnectionPool(
    min_connections=5,   # Minimum idle connections
    max_connections=20   # Maximum total connections
)

# Use connection
with pool.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
```

**Pool Monitoring:**

```python
# Check pool status
pool_stats = pool.get_pool_stats()
print(f"Active: {pool_stats['active']}, Idle: {pool_stats['idle']}")
```

**Best Practices:**
- Set `min_connections` based on baseline load (typically 2-5)
- Set `max_connections` to prevent resource exhaustion (20-50)
- Always use context managers to ensure connections are returned
- Monitor pool saturation: if `active == max`, increase `max_connections`

---

## PostgreSQL-Specific Tuning

### Configuration (`postgresql.conf`)

```ini
# Connection Settings
max_connections = 100
shared_buffers = 256MB          # 25% of RAM
effective_cache_size = 1GB      # 50-75% of RAM
work_mem = 4MB                  # Per-operation memory
maintenance_work_mem = 64MB     # For VACUUM, CREATE INDEX

# Query Planner
random_page_cost = 1.1          # SSD: 1.1, HDD: 4.0
effective_io_concurrency = 200  # SSD: 200, HDD: 2

# Write-Ahead Logging (WAL)
wal_buffers = 16MB
checkpoint_completion_target = 0.9
max_wal_size = 1GB

# Logging (for optimization)
log_min_duration_statement = 500  # Log queries > 500ms
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '
log_statement = 'none'
log_duration = off
```

### Maintenance Tasks

**VACUUM (reclaim space, update statistics):**

```bash
# Daily automatic vacuum (via cron)
0 2 * * * psql -U snflwr -d snflwr_db -c "VACUUM ANALYZE;"

# Manual vacuum for specific table
VACUUM ANALYZE messages;

# Full vacuum (requires table lock)
VACUUM FULL messages;
```

**REINDEX (rebuild indexes):**

```sql
-- Rebuild all indexes for a table
REINDEX TABLE messages;

-- Rebuild specific index
REINDEX INDEX idx_messages_session_time;

-- Rebuild database (monthly maintenance)
REINDEX DATABASE snflwr_db;
```

**ANALYZE (update query planner statistics):**

```sql
-- Analyze all tables
ANALYZE;

-- Analyze specific table
ANALYZE messages;

-- View statistics
SELECT * FROM pg_stats WHERE tablename = 'messages';
```

### Slow Query Log

**Enable slow query logging:**

```ini
# postgresql.conf
log_min_duration_statement = 1000  # Log queries > 1 second
```

**Analyze slow queries:**

```bash
# Install pg_badger
sudo apt-get install pgbadger

# Generate report
pgbadger /var/log/postgresql/postgresql-16-main.log -o report.html
```

---

## SQLite-Specific Tuning

### Pragmas (apply at connection)

```python
import sqlite3

conn = sqlite3.connect('snflwr.db')

# Performance optimizations
conn.execute("PRAGMA journal_mode=WAL")           # Write-Ahead Logging (better concurrency)
conn.execute("PRAGMA synchronous=NORMAL")         # Balanced safety/performance
conn.execute("PRAGMA cache_size=-64000")          # 64MB cache
conn.execute("PRAGMA temp_store=MEMORY")          # Store temp tables in RAM
conn.execute("PRAGMA mmap_size=268435456")        # 256MB memory-mapped I/O
conn.execute("PRAGMA page_size=4096")             # Match OS page size
conn.execute("PRAGMA auto_vacuum=INCREMENTAL")    # Automatic space reclamation
```

**WAL Mode Benefits:**
- Multiple readers + 1 writer (no blocking)
- Better concurrency for web applications
- Faster writes
- Automatic checkpointing

**Check WAL mode:**

```bash
sqlite3 snflwr.db "PRAGMA journal_mode;"
# Should return: wal
```

### SQLite Maintenance

**Analyze database:**

```bash
sqlite3 snflwr.db "ANALYZE;"
```

**Optimize database (monthly):**

```bash
sqlite3 snflwr.db "VACUUM; REINDEX; ANALYZE;"
```

**Check database integrity:**

```bash
sqlite3 snflwr.db "PRAGMA integrity_check;"
```

---

## Monitoring & Analysis

### Key Metrics to Monitor

**PostgreSQL:**

```sql
-- Connection count
SELECT count(*) FROM pg_stat_activity;

-- Long-running queries
SELECT
    pid,
    now() - query_start AS duration,
    query,
    state
FROM pg_stat_activity
WHERE state = 'active'
  AND now() - query_start > interval '5 seconds'
ORDER BY duration DESC;

-- Table sizes
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Cache hit ratio (should be > 99%)
SELECT
    sum(heap_blks_hit) / nullif(sum(heap_blks_hit + heap_blks_read), 0) * 100 AS cache_hit_ratio
FROM pg_statio_user_tables;
```

**SQLite:**

```python
import sqlite3

conn = sqlite3.connect('snflwr.db')

# Database size
size = conn.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()").fetchone()[0]
print(f"Database size: {size / 1024 / 1024:.2f} MB")

# Table row counts
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for table in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
    print(f"{table[0]}: {count} rows")
```

### Performance Testing

**Benchmark queries:**

```python
import time
import statistics

def benchmark_query(db, query, params, iterations=100):
    """Benchmark a query over N iterations"""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        result = db.execute_read(query, params)
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return {
        'mean': statistics.mean(times),
        'median': statistics.median(times),
        'p95': statistics.quantiles(times, n=20)[18],  # 95th percentile
        'p99': statistics.quantiles(times, n=100)[98],
        'min': min(times),
        'max': max(times)
    }

# Example usage
query = "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT 20"
stats = benchmark_query(db_manager, query, ('test_session',), iterations=100)
print(f"Mean: {stats['mean']:.2f}ms, P95: {stats['p95']:.2f}ms")
```

---

## Best Practices

### DO ✅

1. **Use indexes for:**
   - WHERE clauses
   - JOIN conditions
   - ORDER BY columns
   - Foreign keys

2. **Optimize queries:**
   - Use LIMIT for pagination
   - Avoid SELECT * (specify columns)
   - Use JOINs instead of subqueries
   - Filter early (WHERE before JOIN)

3. **Connection management:**
   - Use connection pooling (PostgreSQL)
   - Close connections properly
   - Set appropriate timeouts

4. **Regular maintenance:**
   - VACUUM weekly (PostgreSQL)
   - ANALYZE after bulk inserts
   - Monitor query performance
   - Review slow query logs

5. **Caching:**
   - Cache frequently accessed data (Redis)
   - Use appropriate TTLs
   - Invalidate cache on updates

### DON'T ❌

1. **Avoid:**
   - SELECT * in production
   - N+1 query patterns
   - Long-running transactions
   - Unindexed foreign keys
   - Over-indexing (hurts writes)

2. **Don't:**
   - Fetch entire tables
   - Use LIKE '%term%' without full-text search
   - Run VACUUM FULL in production (locks table)
   - Ignore query warnings
   - Skip connection pooling (PostgreSQL)

### Query Checklist

Before deploying a new query:

- [ ] Is it using indexes? (EXPLAIN ANALYZE)
- [ ] Is response time < 100ms for 95% of requests?
- [ ] Does it handle pagination?
- [ ] Are there any N+1 query patterns?
- [ ] Is connection properly closed?
- [ ] Is result cached if appropriate?
- [ ] Are SQL injection vulnerabilities prevented? (parameterized queries)

---

## Migration from SQLite to PostgreSQL

When scaling beyond 1000 users, migrate to PostgreSQL:

```bash
# 1. Backup SQLite
python scripts/backup_database.py backup

# 2. Export SQLite data
sqlite3 snflwr.db .dump > dump.sql

# 3. Convert to PostgreSQL format
# Use pgloader or manual conversion

# 4. Create PostgreSQL database
createdb -U snflwr snflwr_db

# 5. Initialize schema
psql -U snflwr -d snflwr_db -f database/schema.sql

# 6. Import data
psql -U snflwr -d snflwr_db -f dump.sql

# 7. Create indexes
python database/add_performance_indexes.py

# 8. Update .env
DATABASE_TYPE=postgresql
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=snflwr
POSTGRES_PASSWORD=secure_password
POSTGRES_DB=snflwr_db

# 9. Test application
python api/server.py

# 10. Verify data integrity
# Compare row counts, spot-check data
```

---

## Resources

- **PostgreSQL Performance Tuning:** https://wiki.postgresql.org/wiki/Performance_Optimization
- **SQLite Optimization:** https://www.sqlite.org/optoverview.html
- **Index Strategy:** https://use-the-index-luke.com/
- **Explain Plans:** https://explain.depesz.com/

---

**Document Version:** 1.0
**Last Updated:** 2025-12-25
**Next Review:** 2026-03-25 (quarterly)
