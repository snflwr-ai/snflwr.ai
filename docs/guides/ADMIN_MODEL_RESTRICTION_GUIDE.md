# How to Restrict Models for Students in Open WebUI

The `MODEL_FILTER_LIST` environment variable doesn't work in all Open WebUI versions. Here's how to restrict models using the Admin Panel instead:

## Method 1: Admin Panel Settings (Recommended)

### Step 1: Login as Admin on Port 3000

1. Go to http://localhost:3000
2. Login with your admin account

### Step 2: Access Admin Settings

1. Click your **profile icon** (top right corner)
2. Select **Admin Panel** (or **Settings** → **Admin**)
3. Look for **Settings** or **Admin Settings** in the sidebar

### Step 3: Configure Model Restrictions

Look for one of these sections:

#### Option A: "Models" Section
1. Find **Settings → Admin Settings → Models**
2. Look for **"Model Visibility"** or **"Available Models"**
3. Uncheck all models except:
   - ☑ snflwr-ai:latest (for students/children)
4. Save changes
5. Admins/parents use the base chat model (e.g., `qwen3.5:9b`) directly — no custom modelfile needed

#### Option B: "Permissions" Section
1. Find **Settings → Admin Settings → Permissions**
2. Look for **"User Permissions"** or **"Default User Permissions"**
3. Under **"Models"**, select:
   - snflwr-ai:latest
4. Uncheck **"Allow users to see all models"** or **"Can access all models"**
5. Save changes

#### Option C: Per-User Restrictions
1. Go to **Admin Panel → Users**
2. Click on the student account
3. Look for **"Permissions"** or **"Allowed Models"**
4. Set allowed models to only:
   - snflwr-ai:latest
5. Save

---

## Method 2: Use Only Port 3001 for Students (Current Best Option)

Since environment variables might not work, we can control this differently:

### Current Setup:
- **Port 3000**: No model restrictions (for admin/testing)
- **Port 3001**: Attempts to restrict models (not working yet)

### Workaround - Separate Ollama Instance:

Create a dedicated Ollama instance with ONLY safe models:

#### Step 1: Stop K-12 Instance
```bash
cd loving-morse/frontend/open-webui
docker-compose -f docker-compose.k12.yaml down
```

#### Step 2: Start K-12 Ollama with Only Safe Models

Create: `docker-compose.ollama-k12.yaml`
```yaml
services:
  ollama-k12:
    image: ollama/ollama:latest
    container_name: ollama-k12
    ports:
      - "11435:11434"  # Different port
    volumes:
      - ollama-k12:/root/.ollama
    restart: unless-stopped

volumes:
  ollama-k12: {}
```

Start it:
```bash
docker-compose -f docker-compose.ollama-k12.yaml up -d
```

#### Step 3: Pull Only Safe Models to K-12 Ollama
```bash
# Create snflwr-ai on K-12 Ollama
docker exec ollama-k12 ollama pull qwen3.5:9b
docker cp models/Snflwr_AI_Kids.modelfile ollama-k12:/tmp/
docker exec ollama-k12 ollama create snflwr-ai:latest -f /tmp/Snflwr_AI_Kids.modelfile
```

#### Step 4: Point K-12 Open WebUI to K-12 Ollama

Edit `docker-compose.k12.yaml`:
```yaml
environment:
  - 'OLLAMA_BASE_URL=http://ollama-k12:11434'  # Use K-12 Ollama
```

Add network:
```yaml
services:
  open-webui-k12:
    networks:
      - ollama-k12-network

networks:
  ollama-k12-network:
    external: true
    name: ollama-k12_default
```

Restart:
```bash
docker-compose -f docker-compose.k12.yaml up -d
```

**Result:**
- Port 3000 → Main Ollama (all models)
- Port 3001 → K-12 Ollama (only `snflwr-ai:latest` exists)
- Students physically cannot access unsafe models

---

## Method 3: Check Open WebUI Version

Different versions have different config options. Check version:

```bash
docker exec open-webui cat /app/CHANGELOG.md | head -20
```

Or check in UI:
1. Go to **Settings → About**
2. Check version number

Then search for docs specific to that version.

---

## Testing Current Setup

1. Login to port 3000 as student
2. Check what models you see
3. If you see all models → Need UI restrictions (Method 1)
4. If UI restrictions don't exist → Use separate Ollama (Method 2)

---

## Recommended Solution

For production K-12 deployment, **Method 2 (Separate Ollama)** is best because:
- ✅ Physical isolation - unsafe models don't exist
- ✅ Can't be bypassed
- ✅ No reliance on UI permissions
- ✅ Clearest security boundary

Would you like me to set up Method 2?
