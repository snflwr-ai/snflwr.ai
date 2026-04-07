---
---

# Open WebUI Admin Setup - K-12 Model Restrictions

## Setting Model Permissions via Admin Panel

The environment variables for user permissions might not work in all Open WebUI versions. Here's how to set them via the admin UI:

### Step 1: Login as Admin

1. Go to http://localhost:3000
2. Login with your admin account

### Step 2: Access Admin Settings

1. Click the **profile icon** (top right)
2. Click **Admin Panel** or **Settings**
3. Look for **Admin Settings** section

### Step 3: Set Model Permissions

#### Option A: Workspace Settings (if available)
1. Go to **Settings → Admin Panel → Workspace**
2. Under **Models**, configure:
   - **Default Model**: `snflwr.ai`
   - **Available Models**: Check only:
     - ☑ snflwr.ai (for students/children)
   - **Model Visibility**: Set to "Custom" or "Restricted"
   - Admins/parents use the base chat model (e.g., `qwen3.5:9b`) directly — no custom modelfile needed

#### Option B: User Settings (per-user basis)
1. Go to **Settings → Admin Panel → Users**
2. Click on the student account
3. Under **Permissions** or **Models**:
   - Set allowed models to: `snflwr.ai`
   - Disable "Can access all models"

#### Option C: Role-Based (if available)
1. Go to **Settings → Admin Panel → Roles** (if exists)
2. Create a "Student" role with:
   - Limited model access
   - No admin permissions
3. Assign student accounts to "Student" role

---

## If Admin Settings Don't Have Model Restrictions

Some Open WebUI versions don't support granular model permissions via UI. In that case, use the **Separate Instance Method**:

### Create K-12 Student Instance

Create a new docker-compose file for students only:

**File: `frontend/open-webui/docker-compose.k12.yaml`**

```yaml
services:
  open-webui-k12:
    image: ghcr.io/open-webui/open-webui:main
    container_name: snflwr-k12
    ports:
      - "3001:8080"  # Different port for students
    volumes:
      - open-webui-k12:/app/backend/data
    environment:
      - 'OLLAMA_BASE_URL=http://host.docker.internal:11434'
      - 'WEBUI_SECRET_KEY='
      # HARD FILTER - affects everyone on this instance
      - 'MODEL_FILTER_LIST=snflwr.ai'
      - 'ENABLE_MODEL_FILTER=true'
      - 'DEFAULT_MODELS=snflwr.ai'
      - 'ENABLE_SIGNUP=false'  # Parent/teacher creates accounts
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped

volumes:
  open-webui-k12: {}
```

**Start K-12 instance:**
```bash
cd loving-morse/frontend/open-webui
docker-compose -f docker-compose.k12.yaml up -d
```

**Result:**
- Admin uses: http://localhost:3000 (all models, full access)
- Students use: http://localhost:3001 (only `snflwr.ai`, locked down)

---

## Quick Fix: Use MODEL_FILTER_LIST for Now

If you want to test the student experience right now, temporarily enable hard filtering:

1. Edit `docker-compose.yaml`:
```yaml
environment:
  - 'MODEL_FILTER_LIST=snflwr.ai'
  - 'ENABLE_MODEL_FILTER=true'
```

2. Restart:
```bash
docker-compose down && docker-compose up -d
```

3. **Everyone** (including admin) will only see `snflwr.ai`
4. This lets you test the student experience
5. Remove those lines to get full admin access back

---

## Checking Current Open WebUI Version

Different versions have different features. Check your version:

```bash
docker exec open-webui cat /app/backend/version.txt
# OR
docker logs open-webui | grep -i version
```

---

## Alternative: API-Based Restrictions

If UI controls don't work, you can restrict via API middleware. Let me know if you need this approach.
