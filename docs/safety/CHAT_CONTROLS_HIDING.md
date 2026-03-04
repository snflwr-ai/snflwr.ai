# Chat Controls UI Hiding for K-12 Safety

## Overview
This document describes the frontend modifications made to hide Chat Controls from non-admin users (students) in Open WebUI. This prevents students from seeing or attempting to toggle safety filters, which is critical for COPPA compliance and child safety.

## Problem
Students could access the Chat Controls menu in the UI, which displays:
- Filter toggles (Valves)
- System Prompt editor
- Advanced Parameters
- Function configuration

Even though the backend now force-enables the safety filter (see `BACKEND_SAFETY_ENFORCEMENT.md`), students could still see the UI and attempt to toggle filters off, which creates confusion and appears as though they have control over safety settings.

## Solution
Modified the frontend Svelte components to restrict Chat Controls visibility to **admin users only**.

## Files Modified

### 1. Navbar.svelte
**File:** `loving-morse/frontend/open-webui/src/lib/components/chat/Navbar.svelte`

**Line 213 - BEFORE:**
```svelte
{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
```

**Line 213 - AFTER:**
```svelte
{#if $user?.role === 'admin'}
```

**Impact:** The "Controls" button (knobs icon) in the navbar is now only visible to admin users. Students cannot access Chat Controls at all.

### 2. Controls.svelte
**File:** `loving-morse/frontend/open-webui/src/lib/components/chat/Controls/Controls.svelte`

**Changes:**

| Line | Component | Before | After |
|------|-----------|--------|-------|
| 33 | Main wrapper | `$user?.role === 'admin' \|\| ($user?.permissions.chat?.controls ?? true)` | `$user?.role === 'admin'` |
| 66 | Valves section | `$user?.role === 'admin' \|\| ($user?.permissions.chat?.valves ?? true)` | `$user?.role === 'admin'` |
| 76 | System Prompt | `$user?.role === 'admin' \|\| ($user?.permissions.chat?.system_prompt ?? true)` | `$user?.role === 'admin'` |
| 93 | Advanced Params | `$user?.role === 'admin' \|\| ($user?.permissions.chat?.params ?? true)` | `$user?.role === 'admin'` |

**Impact:** Even if a student somehow accesses the Controls panel, all sections are hidden unless they have admin role.

## Technical Details

### Original Permission System
Open WebUI used a fallback permission system:
```svelte
{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
```

The `?? true` operator means: "if the permission is undefined, default to TRUE (show the controls)."

This was insecure for K-12 deployments because:
- Students would see controls by default
- Permissions needed to be explicitly set to `false` to hide controls
- Easy to accidentally grant students access

### New Admin-Only System
Changed to a simple role check:
```svelte
{#if $user?.role === 'admin'}
```

**Benefits:**
- Default-deny approach (secure by default)
- Only admins see Chat Controls
- No configuration needed - role-based only
- Simpler, less error-prone

## Deployment

### Building the Custom Image
Since Svelte files are compiled to JavaScript at build time, the Docker image needs to be rebuilt:

```bash
cd /path/to/snflwr-ai/frontend/open-webui
docker build -t custom-open-webui .
```

### Updating the Running Container
After building, stop the current container and start with the new image:

```bash
docker stop open-webui
docker run -d \
  --name open-webui \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  --add-host=host.docker.internal:host-gateway \
  custom-open-webui
```

## Testing

### Test as Admin
1. Log in as admin user
2. Start a chat
3. **Expected:** Controls button (knobs icon) visible in navbar
4. Click Controls
5. **Expected:** Can see and configure Valves, System Prompt, Advanced Params

### Test as Student
1. Log in as `student@test.com` / `student123`
2. Start a chat
3. **Expected:** NO Controls button visible in navbar
4. **Expected:** No way to access filter settings

## Combined Security Architecture

This frontend hiding works in conjunction with backend enforcement:

| Layer | Component | What It Does |
|-------|-----------|--------------|
| **Frontend** | Navbar.svelte, Controls.svelte | Hides Chat Controls UI from students |
| **Backend** | filter.py (get_active_status) | Forces safety filter active for non-admin users |
| **Filter Function** | openwebui_safety_filter_age_adaptive.py | Runs age-adaptive content filtering |

**Defense in Depth:**
1. **Frontend:** Students can't see controls (convenience, reduces confusion)
2. **Backend:** Even if they bypass UI, filter is force-enabled (security)
3. **Filter:** Even if they bypass backend, admin-only bypass in inlet() (last line of defense)

## Legal Compliance

### COPPA Requirements
- Students under 13 cannot control their own privacy/safety settings
- Platform must enforce protections, not rely on child's choices
- Hiding controls ensures children cannot accidentally disable protections

### Educational Best Practices
- Students focus on learning, not system configuration
- Teachers and admins maintain control over safety settings
- Clear separation of roles (student vs. admin)

## Rollback Procedure

If you need to revert to the original permission system:

### Navbar.svelte Line 213
```svelte
{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
```

### Controls.svelte
```svelte
Line 33:  {#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
Line 66:  {#if $user?.role === 'admin' || ($user?.permissions.chat?.valves ?? true)}
Line 76:  {#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
Line 93:  {#if $user?.role === 'admin' || ($user?.permissions.chat?.params ?? true)}
```

Then rebuild the Docker image.

## Future Enhancements

1. **Add Student Role:** Create explicit `student` role instead of just `user`
2. **Permission Groups:** Define preset permission groups (Teacher, Student, Admin)
3. **Audit Logging:** Log when admins access Chat Controls
4. **UI Indicator:** Show admins a badge when they have elevated access
5. **Classroom Mode:** Teachers can temporarily enable certain controls for guided lessons

## Maintenance Notes

- When updating Open WebUI, check if these files have changed upstream
- Always test both admin and student views after updates
- Keep this documentation in sync with any permission system changes
- Frontend changes require full Docker rebuild (not just file copy like backend)
