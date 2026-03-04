# snflwr.ai - Open WebUI Integration Guide

**Last Updated:** 2025-12-20
**Status:** In Progress - Frontend Components Complete

---

## Overview

snflwr.ai is built as an **extension to Open WebUI**, leveraging its existing infrastructure rather than creating duplicate systems. This document outlines the integration strategy.

## Architecture Philosophy

**Key Principle:** Use as much of Open WebUI's frontend as possible.

Instead of building a separate application, Snflwr:
- **Extends** Open WebUI with child profile management
- **Enhances** chat routing with safety filtering
- **Reuses** Open WebUI's authentication, UI components, and routing

---

## Component Integration

### 1. Authentication

**Approach:** Use Open WebUI's existing auth system entirely.

- **Open WebUI handles:** User registration, login, session management
- **Snflwr adds:** Child profile association with Open WebUI user IDs
- **Backend mapping:** `parent_id` in Snflwr DB = `user.id` from Open WebUI

**Files:**
- Open WebUI auth: `frontend/open-webui/src/routes/auth/+page.svelte`
- Snflwr API client: `frontend/open-webui/src/lib/apis/snflwr/index.ts`

**Flow:**
1. Parent logs in through Open WebUI's auth page
2. On successful login, Open WebUI sets `$user` store
3. Snflwr components use `$user.id` as `parent_id` for API calls
4. No separate Snflwr login required

### 2. UI Components

**Approach:** Use Open WebUI's common components exclusively.

**Snflwr components now use:**
```typescript
import Modal from '$lib/components/common/Modal.svelte';
import Spinner from '$lib/components/common/Spinner.svelte';
import Badge from '$lib/components/common/Badge.svelte';  // Available
import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';  // Available
```

**Benefits:**
- Consistent styling (TailwindCSS + Open WebUI's theme)
- Dark mode support out of the box
- Focus trapping, ESC key handling, accessibility
- Reduced bundle size (no duplicate components)

**Snflwr-specific components:**
- `ProfileSelector.svelte` - Modal for selecting child profile (uses Open WebUI Modal)
- `CreateProfileModal.svelte` - Form for creating profiles (uses Open WebUI Modal)
- `ProfileIndicator.svelte` - Sidebar widget showing active profile

### 3. State Management

**Approach:** Integrate with Open WebUI's store system.

**Open WebUI stores (reused):**
- `$user` - Current logged-in parent
- `$config` - Backend configuration
- `$chats`, `$models` - Chat and model management

**Snflwr stores (new):**
- `$childProfiles` - List of child profiles for current parent
- `$activeChildProfile` - Currently selected child
- `$showProfileSelector` - Modal visibility
- `$safetyAlerts` - Safety alerts for parent

**File:** `frontend/open-webui/src/lib/stores/snflwr.ts`

### 4. Routing & Layout

**Approach:** Hook into Open WebUI's existing route structure.

**Modified Open WebUI files:**
- `frontend/open-webui/src/routes/(app)/+layout.svelte`
  - Added profile loading on mount
  - Imports `ProfileSelector` component

- `frontend/open-webui/src/lib/components/layout/Sidebar.svelte`
  - Added `ProfileIndicator` display

**Flow:**
1. User logs in → Open WebUI auth sets `$user`
2. Layout component loads → Calls `getChildProfiles($user.id)`
3. If profiles exist → Show `ProfileSelector` modal
4. User selects profile → Stored in `$activeChildProfile`
5. Chat requests include `profile_id` for safety filtering

---

## Backend Integration

### API Server Architecture

**Two Parallel APIs:**
1. **Open WebUI API** (existing) - Handles general chat, models, users
2. **Snflwr API** (new) - Handles profiles, safety, analytics

**Snflwr API Base:** `http://localhost:8000`

**Endpoints:**
```
/health                              GET   Health check
/api/profiles/                       POST  Create child profile
/api/profiles/parent/{parent_id}     GET   Get all profiles for parent
/api/profiles/{profile_id}           GET   Get specific profile
/api/profiles/{profile_id}           PATCH Update profile
/api/profiles/{profile_id}           DELETE Deactivate profile
/api/safety/alerts/{parent_id}       GET   Get safety alerts
/api/safety/incidents/{profile_id}   GET   Get safety incidents
/api/analytics/usage/{profile_id}    GET   Usage statistics
```

**Authentication:**
- Snflwr API uses session tokens from Snflwr backend
- Open WebUI `user.id` becomes Snflwr `parent_id`
- Profile operations require valid session

### Chat Flow with Safety Filter

**Current (Open WebUI only):**
```
User → Open WebUI Frontend → Ollama → Response
```

**Future (with Snflwr):**
```
User → Open WebUI Frontend → Snflwr API → 5-Stage Safety Pipeline → Ollama → Response
```

**5-Stage Safety Pipeline:**
1. **Input Validation** - Length, encoding, injection detection
2. **Normalization** - Unicode, whitespace, obfuscation removal
3. **Pattern Matcher** - Keyword-based blocking (fast)
4. **Semantic Classifier** - LLM-based safety classification (context-aware)
5. **Age Gate** - Grade-level content enforcement

---

## Configuration

### Environment Variables

**Snflwr-specific:**
```bash
# Snflwr API (in frontend/.env)
PUBLIC_SNFLWR_API_URL=http://localhost:8000

# Backend config (in config.py)
SNFLWR_DB_PATH=data/snflwr.db
OLLAMA_HOST=http://localhost:11434
LOG_LEVEL=INFO
```

**Open WebUI uses its own environment variables** - Snflwr doesn't modify them.

### Model Configuration

**Hardware-based model selection:**
The chat model is chosen from the qwen3.5 family based on available hardware:
```python
# config.py
MODEL_CONFIG = {
    'default': {
        'model_name': 'qwen3.5:9b',
        'context_window': 32768,  # 32K
        'ram_required': '8GB+'
    },
    'large': {
        'model_name': 'qwen3.5:27b',
        'context_window': 131072,  # 128K
        'ram_required': '24GB+'
    }
}
```

---

## Development Workflow

### Starting the Application

**Terminal 1 - Snflwr Backend:**
```bash
cd /path/to/snflwr.ai
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn api.server:app --reload --port 8000
```

**Terminal 2 - Open WebUI Frontend:**
```bash
cd frontend/open-webui
npm install
npm run dev
```

**Terminal 3 - Ollama (if using local models):**
```bash
ollama serve
ollama pull qwen3.5:9b
ollama pull qwen3.5:27b
```

### Testing

**Backend API Tests:**
```bash
# Test authentication
bash test_auth_api.sh

# Test profiles
bash test_profile_api.sh

# Test backend auth manager
python test_auth_backend.py
```

**Frontend (manual):**
1. Navigate to `http://localhost:3000` (Open WebUI)
2. Login with Open WebUI account
3. Profile selector should appear (if Snflwr API is running)
4. Create/select child profile
5. Chat requests should route through Snflwr

---

## File Structure

```
snflwr.ai/
├── frontend/open-webui/          # Open WebUI frontend (modified)
│   └── src/
│       ├── lib/
│       │   ├── apis/snflwr/   # Snflwr API client ✓
│       │   ├── stores/
│       │   │   └── snflwr.ts  # Snflwr state ✓
│       │   └── components/
│       │       ├── snflwr/    # Snflwr-specific components ✓
│       │       │   ├── ProfileSelector.svelte
│       │       │   ├── CreateProfileModal.svelte
│       │       │   └── ProfileIndicator.svelte
│       │       ├── common/       # Open WebUI components (reused)
│       │       │   ├── Modal.svelte
│       │       │   └── Spinner.svelte
│       │       └── layout/
│       │           └── Sidebar.svelte  # Modified ✓
│       └── routes/
│           ├── auth/+page.svelte      # Open WebUI auth (unchanged)
│           └── (app)/+layout.svelte   # Modified for profile loading ✓
│
├── api/                          # Snflwr FastAPI backend
│   ├── server.py                 # Main FastAPI app ✓
│   └── routes/
│       ├── profiles.py           # Profile CRUD ✓
│       ├── auth.py               # Authentication ✓
│       ├── chat.py               # Chat with safety filter (partial)
│       ├── safety.py             # Safety endpoints (stub)
│       └── analytics.py          # Analytics endpoints (stub)
│
├── core/                         # Business logic
│   ├── profile_manager.py        # Profile CRUD ✓
│   └── authentication.py         # Auth manager ✓
│
├── database/
│   ├── schema.sql                # Database schema (13 tables) ✓
│   └── init_db.py                # DB initialization ✓
│
├── storage/
│   └── database.py               # Database manager ✓
│
├── config.py                     # Configuration ✓
│
├── test_auth_api.sh              # API tests ✓
├── test_auth_backend.py          # Backend tests ✓
└── test_profile_api.sh           # Profile API tests ✓
```

**Legend:**
- ✓ = Complete and tested
- (partial) = Implemented but incomplete
- (stub) = Placeholder only

---

## Integration Checklist

### ✅ Completed

- [x] Database schema (13 tables, age 0-18 validation)
- [x] Profile CRUD backend (create, read, update, deactivate)
- [x] Authentication backend (register, login, logout, validate)
- [x] FastAPI server with route structure
- [x] Profile API endpoints (tested with curl)
- [x] Authentication API endpoints (tested with curl)
- [x] Frontend API client (`apis/snflwr/index.ts`)
- [x] Frontend state management (`stores/snflwr.ts`)
- [x] ProfileSelector component (using Open WebUI Modal)
- [x] CreateProfileModal component (using Open WebUI Modal)
- [x] ProfileIndicator sidebar widget
- [x] Integration with Open WebUI layout
- [x] Refactor to use Open WebUI components (Modal, Spinner)
- [x] Remove duplicate auth components

### ✅ Completed

- [x] **Chat routing through Snflwr API**
  - Open WebUI chat requests intercepted by middleware
  - Route through 5-stage safety pipeline
  - Return to Open WebUI for display

- [x] **Safety pipeline implementation**
  - Stage 1: Input validation
  - Stage 2: Normalization
  - Stage 3: Pattern matching
  - Stage 4: Semantic classification
  - Stage 5: Age gate

### ⏳ Pending

- [ ] Ollama model integration and testing
- [ ] Safety incident logging
- [ ] Parent dashboard for monitoring
- [ ] Safety alerts display
- [ ] Usage analytics implementation
- [ ] Session management improvements
- [ ] Authentication middleware for API protection
- [ ] End-to-end testing (frontend + backend)
- [ ] Deployment configuration
- [ ] Documentation for parents

---

## Key Differences from Standalone App

| Aspect | Standalone App | Open WebUI Integration |
|--------|----------------|------------------------|
| **Authentication** | Separate login system | Uses Open WebUI's auth |
| **UI Components** | Custom components | Open WebUI's components |
| **Routing** | Custom routes | Extends Open WebUI routes |
| **State Management** | Standalone stores | Integrates with Open WebUI stores |
| **Deployment** | Separate deployment | Runs alongside Open WebUI |
| **User Base** | New user accounts | Existing Open WebUI users |

---

## Benefits of This Approach

1. **Reduced Development Time**: Reuse existing UI components, auth system, chat interface
2. **Consistent UX**: Users don't need to learn a new interface
3. **Smaller Bundle Size**: No duplicate components or libraries
4. **Easier Maintenance**: Updates to Open WebUI benefit Snflwr automatically
5. **Seamless Integration**: Feels like a native Open WebUI feature
6. **Lower Barrier to Entry**: Existing Open WebUI users can enable Snflwr with one click

---

## Future Enhancements

### Phase 1 (Current Sprint)
- Complete safety filter pipeline
- Integrate chat routing
- Test end-to-end flow

### Phase 2
- Parent dashboard with analytics
- Real-time safety alerts
- Email notifications
- Usage quotas and limits

### Phase 3
- Mobile-optimized UI
- Offline mode support
- Multi-language support
- Advanced analytics and reporting

---

## Troubleshooting

### Profile Selector Not Appearing
1. Check Snflwr API is running: `curl http://localhost:8000/health`
2. Check browser console for errors
3. Verify Open WebUI user is logged in (`$user` store should be set)
4. Check profile API: `curl http://localhost:8000/api/profiles/parent/{user_id}`

### Styling Issues
- Ensure Open WebUI's TailwindCSS is loaded
- Check dark mode class on `<html>` element
- Use Open WebUI's color classes (not custom ones)

### API Connection Errors
- Verify `PUBLIC_SNFLWR_API_URL` in frontend/.env
- Check CORS settings in Snflwr API server
- Ensure both servers are running (Open WebUI + Snflwr)

---

## Resources

- **Open WebUI Repo:** https://github.com/open-webui/open-webui
- **Snflwr Production Roadmap:** `PRODUCTION_ROADMAP.md`
- **Testing Results:** `TESTING_RESULTS.md`
- **Database Schema:** `database/schema.sql`
- **API Tests:** `test_auth_api.sh`, `test_profile_api.sh`

---

**For questions or issues, see:** `PRODUCTION_ROADMAP.md` → "Questions to Answer" section
