# Open WebUI K-12 Safety Lockdown Guide

## Overview
This guide explains how to lock down Open WebUI so K-12 students can only access safe, age-appropriate snflwr.ai models.

---

## Method 1: Environment Variable Model Filtering (Recommended for Families)

**What it does:** Only shows whitelisted models in the UI. Other models are completely hidden.

**Configuration:** Already applied in `docker-compose.yaml`

```yaml
environment:
  - 'MODEL_FILTER_LIST=snflwr-ai:latest'
  - 'ENABLE_MODEL_FILTER=true'
  - 'DEFAULT_MODELS=snflwr-ai:latest'
```

**Models shown:**
- ✅ `snflwr-ai:latest` - K-12 tutor (default, custom persona for students/children)
- ❌ All other models hidden (qwen3.5:9b, llama-guard3:1b, etc.)

Admins/parents use the base chat model (e.g., `qwen3.5:9b`) directly -- no custom modelfile needed.

**To apply changes:**
```bash
cd loving-morse/frontend/open-webui
docker-compose down
docker-compose up -d
```

**Pros:**
- Simple, effective
- No UI configuration needed
- Can't be bypassed by users

**Cons:**
- Requires restart to change models
- All users see same models

---

## Method 2: Admin Controls + User Roles (For Schools/Organizations)

**What it does:** Admin can control what each user/role can access.

### Step 1: Enable Authentication
```yaml
environment:
  - 'WEBUI_AUTH=true'
  - 'ENABLE_SIGNUP=false'  # Prevent unauthorized signups
```

### Step 2: Create Admin Account
1. First user to sign up becomes admin
2. Or set via: `DEFAULT_USER_ROLE=admin`

### Step 3: Configure Model Access
**Via Admin Panel (after login):**
1. Go to Settings → Admin Settings → Models
2. Set "Default Model" to `snflwr-ai:latest`
3. Under "Model Visibility", uncheck all models except:
   - snflwr-ai:latest (for students/children)

**Via Environment Variables:**
```yaml
environment:
  # Only admin can see all models
  - 'USER_PERMISSIONS_CHAT_MODELS=snflwr-ai:latest'
  # Disable model switching for non-admin
  - 'USER_PERMISSIONS_CHAT_MODEL_SWITCHING=false'
```

**Pros:**
- Per-user control
- Admin can access other models (for testing/debugging)
- No restart needed to change permissions

**Cons:**
- More complex setup
- Requires user management

---

## Method 3: Custom Deployment (Production)

For production deployments, create a dedicated K-12 instance:

### K-12 Student Instance (docker-compose.k12.yaml)
```yaml
services:
  open-webui-k12:
    image: ghcr.io/open-webui/open-webui:main
    container_name: snflwr-k12
    ports:
      - "3000:8080"
    environment:
      - 'OLLAMA_BASE_URL=http://host.docker.internal:11434'
      # STRICT K-12 LOCKDOWN
      - 'MODEL_FILTER_LIST=snflwr-ai:latest'  # Only tutor
      - 'ENABLE_MODEL_FILTER=true'
      - 'DEFAULT_MODELS=snflwr-ai:latest'
      - 'ENABLE_SIGNUP=false'  # Parent creates accounts
      - 'SHOW_ADMIN_DETAILS=false'  # Hide admin info
      # Disable dangerous features
      - 'ENABLE_COMMUNITY_SHARING=false'
      - 'ENABLE_MESSAGE_RATING=false'
    extra_hosts:
      - host.docker.internal:host-gateway
```

**Usage:**
- Students use: http://localhost:3000 (locked to `snflwr-ai:latest` only)
- Parents/teachers use the base chat model (e.g., `qwen3.5:9b`) directly via the admin Open WebUI instance -- no separate educator container or custom modelfile needed

---

## Method 4: Dedicated Ollama Instance (Maximum Security)

Run separate Ollama instances with only safe models.

### K-12 Ollama Instance
```bash
# Create separate Ollama data directory
mkdir -p ~/.ollama-k12

# Start separate Ollama on different port
OLLAMA_MODELS=~/.ollama-k12 ollama serve --port 11435
```

### Pull only safe models
```bash
OLLAMA_HOST=http://localhost:11435 ollama pull qwen3.5:9b
OLLAMA_HOST=http://localhost:11435 ollama create snflwr-ai:latest -f models/Snflwr_AI_Kids.modelfile
```

### Point Open WebUI to K-12 Ollama
```yaml
environment:
  - 'OLLAMA_BASE_URL=http://host.docker.internal:11435'  # K-12 Ollama only
```

**Pros:**
- Physical isolation of models
- Impossible to access unsafe models (they don't exist in this instance)
- Best for schools/public deployments

**Cons:**
- More resource usage (2 Ollama instances)
- More complex management

---

## Recommended Configurations by Use Case

### Home Use (Single Family)
**Use Method 1** - Environment variable filtering
- Simple setup
- All family members see safe models only
- Parent can access admin Ollama directly if needed

### Small School/Classroom (10-30 students)
**Use Method 2** - Admin controls
- Teacher = admin (access to all features)
- Students = users (locked to snflwr-ai only)
- Easy to add/remove students

### Large School/District (100+ students)
**Use Method 3 + 4** - Separate instances
- Dedicated K-12 Ollama with only safe models
- Separate student and educator portals
- Central admin monitoring

---

## Testing Your Lockdown

### Test 1: Verify Only Safe Models Visible
1. Open http://localhost:3000
2. Click model dropdown (top left)
3. Should only see: `snflwr-ai:latest`

### Test 2: Verify Direct API Access Blocked
```bash
# This should fail if lockdown is working
curl http://localhost:3000/api/models
```

### Test 3: Verify Default Model
1. Start new chat
2. Model should auto-select `snflwr-ai:latest`
3. Student doesn't need to choose

---

## Current Status

✅ **Applied:** Method 1 (Environment variable filtering)

**What's protected:**
- Only `snflwr-ai:latest` visible to students
- Other models (llama3.2, llama-guard3, etc.) are hidden
- Default model set to `snflwr-ai:latest`
- Admins/parents use the base chat model (e.g., `qwen3.5:9b`) directly

**To activate:**
```bash
cd loving-morse/frontend/open-webui
docker-compose down && docker-compose up -d
```

---

## Future Enhancements

### Content Logging (for parent monitoring)
```yaml
environment:
  - 'ENABLE_MESSAGE_RATING=true'  # Parents can review conversations
  - 'WEBHOOK_URL=http://your-logging-service'  # Send copies to parent dashboard
```

### Age-Based Model Selection
Create multiple Open WebUI instances:
- Port 3000: Ages 5-10 (snflwr-ai with elementary prompt)
- Port 3001: Ages 11-14 (snflwr-ai with middle school prompt)
- Port 3002: Ages 15-18 (snflwr-ai with high school prompt)

Each uses different Modelfile configurations for age-appropriate responses.

---

## Security Checklist

- [ ] Model filter enabled
- [ ] Only safe models in whitelist
- [ ] Default model set to age-appropriate option
- [ ] Signup disabled (or admin-only)
- [ ] Admin password strong and secure
- [ ] HTTPS enabled (for production)
- [ ] Session timeouts configured
- [ ] Community sharing disabled
- [ ] External model downloads disabled

---

## Rollback

If you need to see all models again (for testing/debugging):

```yaml
environment:
  - 'OLLAMA_BASE_URL=http://host.docker.internal:11434'
  - 'WEBUI_SECRET_KEY='
  # Comment out or remove these lines:
  # - 'MODEL_FILTER_LIST=snflwr-ai:latest'
  # - 'ENABLE_MODEL_FILTER=true'
```

Then restart:
```bash
docker-compose down && docker-compose up -d
```
