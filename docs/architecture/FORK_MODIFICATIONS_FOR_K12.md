# Open WebUI Fork Modifications for K-12 Safety

## Overview

Since you have a forked version of Open WebUI at `loving-morse/frontend/open-webui`, we can modify the source code to enforce mandatory safety filtering for K-12 students.

## Critical Modifications Needed

### 1. Hide Chat Controls for Non-Admin Users

**File**: `src/lib/components/chat/Navbar.svelte` or `src/lib/components/chat/ChatControls.svelte`

**Problem**: Students can click the Chat Controls button and toggle filters off.

**Solution**: Add role check to hide the button for non-admin users.

```svelte
<script lang="ts">
  import { user } from '$lib/stores';

  $: isAdmin = $user?.role === 'admin';
</script>

{#if isAdmin}
  <!-- Show Chat Controls button -->
  <button on:click={openChatControls}>
    Controls
  </button>
{/if}
```

### 2. Force Safety Filter to Always Be Active

**File**: Backend filter execution logic (likely in Python backend)

**Problem**: Even if hidden, filters can still be disabled via API or database manipulation.

**Solution**: Modify the filter execution logic to ALWAYS call safety filters for non-admin users, regardless of user preferences.

**Location to modify** (needs investigation):
- Backend code that checks if a filter is enabled before calling it
- Add hardcoded check: "If user.role != 'admin' AND filter.name == 'Snflwr Safety Filter', FORCE ENABLE"

### 3. Block API Access for Students

**File**: API middleware or route guards

**Problem**: Students can bypass filters by calling `/api/chat/completions` directly.

**Solution**: Add middleware that rejects API requests from non-admin users.

```python
# In backend API route
@app.post("/api/chat/completions")
async def chat_completions(request: Request, user: User = Depends(get_current_user)):
    if user.role != "admin":
        # For students, redirect to web UI only (which has filters)
        raise HTTPException(
            status_code=403,
            detail="API access restricted. Please use the web interface."
        )
    # ... rest of API logic
```

## Implementation Steps

### Step 1: Find the Chat Controls Button

**Command**:
```bash
grep -r "showControls" loving-morse/frontend/open-webui/src/lib/components/chat/
```

This will find where the Chat Controls button is rendered.

### Step 2: Add Role Check to Hide Button

In the component that renders the Chat Controls button, add:

```svelte
<script>
  import { user } from '$lib/stores';
  $: isStudent = $user?.role === 'user';  // or !== 'admin'
</script>

<!-- Only show controls for admins -->
{#if !isStudent}
  <button class="chat-controls-btn">
    Controls
  </button>
{/if}
```

### Step 3: Force-Enable Safety Filter in Backend

**Find**: Backend code that checks if a function/filter is enabled

**Likely location**:
- `backend/open_webui/routers/functions.py`
- `backend/open_webui/functions/`

**Add logic**:
```python
def should_run_filter(filter_name: str, user: User, user_enabled: bool) -> bool:
    """Determine if a filter should run"""

    # CRITICAL: Safety filter is MANDATORY for non-admin users
    if filter_name == "Snflwr Safety Filter" and user.role != "admin":
        return True  # Force enable regardless of user_enabled

    # For other filters, respect user preference
    return user_enabled
```

### Step 4: Rebuild and Redeploy

After modifications:

```bash
cd loving-morse/frontend/open-webui
docker build -t snflwr-webui:latest .
docker-compose down
docker-compose up -d
```

## Files to Investigate

### Frontend (Svelte/TypeScript)

1. **`src/lib/components/chat/Navbar.svelte`**
   - Likely has the Chat Controls button

2. **`src/lib/components/chat/ChatControls.svelte`**
   - Main Chat Controls component
   - Could add role check here to prevent rendering

3. **`src/lib/stores/index.ts`** or **`src/lib/stores/user.ts`**
   - Check how user role is stored and accessed

4. **`src/routes/+layout.svelte`** or **`src/routes/+page.svelte`**
   - Root layout files where user info is loaded

### Backend (Python/FastAPI)

1. **`backend/open_webui/routers/functions.py`**
   - Filter/function execution logic
   - Where filters are toggled on/off

2. **`backend/open_webui/routers/chats.py`**
   - Chat completion endpoint
   - Where filters are applied to messages

3. **`backend/open_webui/utils/middleware.py`** (if exists)
   - Middleware for API requests
   - Add role-based API blocking

4. **`backend/open_webui/main.py`**
   - Main application entry point
   - Add global middleware

## Quick Win: CSS-Based Hiding (Temporary)

While implementing the full solution, you can add custom CSS to hide the button:

**File**: `src/app.html` or create `static/custom.css`

```css
/* Hide Chat Controls for non-admin users */
body:not(.admin-user) [data-testid="chat-controls"],
body:not(.admin-user) .chat-controls-button {
  display: none !important;
}
```

Then add the `admin-user` class to the body when admin is logged in:

```svelte
<svelte:body class={$user?.role === 'admin' ? 'admin-user' : ''} />
```

## Testing

After modifications:

1. **Test as admin**:
   - Chat Controls button should be visible
   - Can toggle filters on/off

2. **Test as student** (`student@test.com`):
   - Chat Controls button should NOT be visible
   - Safety filter should ALWAYS run (even if somehow disabled in DB)
   - "Let's talk about sex" should be BLOCKED
   - API calls to `/api/chat/completions` should be rejected

## Deployment Checklist

- [ ] Modify Chat Controls button visibility (frontend)
- [ ] Force-enable safety filter for non-admins (backend)
- [ ] Block API access for students (backend middleware)
- [ ] Rebuild Docker image
- [ ] Test with admin account
- [ ] Test with student account
- [ ] Verify filter cannot be disabled by students
- [ ] Verify API is blocked for students

## Rollback Plan

If modifications break something:

```bash
# Switch back to official Open WebUI
docker run -d --name open-webui \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

Then reimplement safety controls at the proxy/middleware level instead of in Open WebUI itself.
