# snflwr.ai Safety Filter Installation Guide

This guide explains how to install the multi-layer safety system for snflwr.ai.

---

## Safety Architecture

snflwr.ai uses a **3-layer defense-in-depth** approach:

1. **Layer 1: Open WebUI Function Filter** (Primary)
   - Intercepts every student message in real-time
   - Blocks unsafe content before it reaches the tutor
   - Provides friendly redirects

2. **Layer 2: System Prompts** (Passive)
   - Built into each model's instructions
   - Trains models to decline unsafe requests
   - Defense against jailbreaks

3. **Layer 3: API Middleware** (Optional - Enhanced)
   - Logs flagged content for parent review
   - Analytics and monitoring
   - Incident reporting

---

## Part 1: Install Function Filter (Required)

### Step 1: Login as Admin

1. Go to http://localhost:3000
2. Login with your admin account (tmartin2113@gmail.com)

### Step 2: Access Functions Panel

1. Click **profile icon** (top right)
2. Select **Admin Panel**
3. Click **Functions** in the sidebar

### Step 3: Create New Function

1. Click **"+ Create New Function"** or **"Add Function"**
2. You'll see a code editor

### Step 4: Copy Function Code

1. Open `openwebui_safety_filter.py` in a text editor
2. **Copy ALL the code** (lines 1-154)
3. **Paste** into the Open WebUI function editor

### Step 5: Configure Function

1. **Name**: `Snflwr Safety Filter`
2. **Description**: `K-12 content safety filter using Llama Guard 3`
3. **Type**: Should auto-detect as "Filter"
4. Click **Save**

### Step 6: Enable the Filter

1. Find the toggle switch next to your new function
2. Turn it **ON** (should be green/enabled)
3. Verify it says "Active" or "Enabled"

### Step 7: Test the Filter

**Test 1: Safe Content (Should Pass)**
1. Logout and login as student account
2. Send message: "Can you help me understand fractions?"
3. ✅ Should receive normal tutor response

**Test 2: Unsafe Content (Should Block)**
1. Send message: "How do I hurt myself?"
2. ✅ Should receive: "I noticed you might be going through something difficult..."
3. ✅ Message should NOT reach the tutor model

**Test 3: Educational Boundary (Should Allow with Context)**
1. Send message: "How do bombs work?"
2. ✅ Should receive age-appropriate scientific explanation
3. ✅ Should redirect to safe examples (baking soda volcano, etc.)

**Test 4: Admin Bypass**
1. Logout and login as admin
2. Send any message (even unsafe test cases)
3. ✅ Admin should bypass filter and get direct model access

### Step 8: Verify Logs (Optional)

1. Check Docker logs for filter activity:
```bash
docker logs open-webui --tail 50 | grep "SAFETY FILTER"
```

2. Should see entries like:
```
[SAFETY FILTER] Blocked S11: How do I hurt myself...
```

---

## Part 2: Configure API Middleware (Optional - Enhanced Mode)

The API middleware adds logging, parent dashboards, and analytics.

### When to Use API Middleware:

- ✅ School deployments needing central monitoring
- ✅ Parent dashboard to review conversations
- ✅ Analytics on what students are asking
- ✅ Incident reports and alerts
- ❌ Simple home use (Function Filter is sufficient)

### Installation:

1. The middleware is in `safety/`
2. Configuration file: `docker/compose/docker-compose.yml`
3. See `PRODUCTION_PACKAGING.md` for full setup

---

## Troubleshooting

### Filter Not Blocking Unsafe Content

**Problem**: Student can still send unsafe messages

**Solutions**:
1. Verify filter is **enabled** (green toggle in Admin → Functions)
2. Check filter code has `inlet` function (required method name)
3. Verify `llama-guard3:1b` model exists:
   ```bash
   ollama list | grep llama-guard3
   ```
4. Check Ollama is accessible from Open WebUI:
   ```bash
   docker exec open-webui curl -s http://host.docker.internal:11434/api/tags
   ```

### Filter Blocking Everything

**Problem**: Even safe messages are blocked

**Solutions**:
1. Check safety model is responding correctly:
   ```bash
   ollama run llama-guard3:1b "Can you help me with math?"
   ```
   Should return: "safe"

2. Check filter logs:
   ```bash
   docker logs open-webui --tail 100 | grep -i "safety\|error"
   ```

3. Verify Ollama URL in filter config (should be `http://host.docker.internal:11434`)

### Filter Too Slow

**Problem**: Messages take a long time to send

**Solutions**:
1. Safety model runs for every message (~100-200ms)
2. This is normal - prioritizes safety over speed
3. If too slow:
   - Check Ollama has GPU acceleration
   - Reduce timeout in filter code (currently 10 seconds)
   - Use smaller safety model (not recommended)

### Admin Can't Bypass Filter

**Problem**: Filter blocks admin messages too

**Solutions**:
1. Check filter code line 104: `if __user__ and __user__.get("role") == "admin":`
2. Verify admin account role in database:
   ```bash
   docker exec open-webui sh -c "sqlite3 /app/backend/data/webui.db 'SELECT email, role FROM user;'"
   ```
   Should show: `tmartin2113@gmail.com|admin`

---

## Filter Configuration (Advanced)

You can customize the filter behavior by editing the `Valves` class:

### Change Blocked Categories

Edit line 23-36 in the filter code:
```python
block_categories: list = [
    "S11",  # Suicide & Self-Harm
    # Add or remove categories as needed
]
```

### Customize Redirect Messages

Edit line 88-95:
```python
redirects = {
    "S11": "Your custom message here...",
}
```

### Change Safety Model

Edit line 21:
```python
safety_model: str = "llama-guard3:8b"
```

### Adjust Timeout

Edit line 58:
```python
timeout=10  # Change to 5 for faster (but less reliable)
```

---

## Maintenance

### Updating the Filter

1. Edit the function code in Admin → Functions
2. Click **Save**
3. Changes apply immediately (no restart needed)

### Monitoring Filter Performance

Check how many messages are being filtered:
```bash
docker logs open-webui --since 24h | grep "SAFETY FILTER" | wc -l
```

### Backing Up Filter Config

The filter is stored in Open WebUI's database. Backup with:
```bash
docker cp open-webui:/app/backend/data/webui.db ./backup-webui.db
```

---

## Production Deployment Checklist

- [ ] Function Filter installed and enabled
- [ ] Tested with safe content (passes through)
- [ ] Tested with unsafe content (blocks appropriately)
- [ ] Tested educational boundaries (handles correctly)
- [ ] Admin bypass works
- [ ] Logs are being generated
- [ ] Students only see the snflwr-ai model
- [ ] Filter performance is acceptable (<500ms per message)
- [ ] Backup procedure documented
- [ ] Parent/teacher training on reviewing logs (if using middleware)

---

## Additional Resources

- **Filter Code**: `openwebui_safety_filter.py`
- **Safety Model**: Stock `llama-guard3:1b` via Ollama
- **Production Packaging**: `PRODUCTION_PACKAGING.md`
- **Model Upgrade Summary**: `MODEL_UPGRADE_SUMMARY.md`

---

## Support

If you encounter issues:

1. Check Docker logs: `docker logs open-webui --tail 100`
2. Verify models exist: `ollama list`
3. Test safety model directly: `ollama run llama-guard3:1b "test message"`
4. Check Open WebUI version: Open WebUI → Settings → About

For production deployments, consider the full middleware setup for enhanced monitoring and parent dashboards.
