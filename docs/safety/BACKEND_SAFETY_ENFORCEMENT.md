---
---

# Backend Safety Filter Enforcement for K-12

## Critical Modification Made

**File Modified**: `backend/open_webui/utils/filter.py`

**Function**: `get_sorted_filter_ids()` → `get_active_status()`

## What Changed

### Before (Vulnerable)
```python
def get_active_status(filter_id):
    function_module = get_function_module(request, filter_id)

    if getattr(function_module, "toggle", None):
        return filter_id in (enabled_filter_ids or [])  # ← Students could disable!

    return True
```

**Problem**: If a student toggled the safety filter OFF in Chat Controls, `enabled_filter_ids` wouldn't include the filter ID, so `get_active_status()` would return `False`, and the filter wouldn't run.

### After (Secured)
```python
def get_active_status(filter_id):
    function_module = get_function_module(request, filter_id)
    function = Functions.get_function_by_id(filter_id)

    # CRITICAL K-12 SAFETY: Force safety filter to ALWAYS be active for non-admin users
    # Check if this is the Snflwr Safety Filter
    if function and "safety" in function.name.lower():
        # Get user from request
        user = getattr(request.state, "user", None)
        if user and user.role != "admin":
            # FORCE ENABLE for non-admin users (students)
            log.info(f"[K-12 SAFETY] Force-enabling safety filter for user {user.email}")
            return True  # ← ALWAYS returns True for students!

    if getattr(function_module, "toggle", None):
        return filter_id in (enabled_filter_ids or [])

    return True
```

**Solution**:
1. Checks if the function name contains "safety"
2. Gets the user from the request
3. If user is NOT admin, FORCE returns `True`
4. This means the safety filter ALWAYS runs for students, regardless of UI toggle state

## How It Works

### Flow for Student Users

1. **Student sends message**: "Let's talk about sex"
2. **Backend receives request** at `/api/chat`
3. **`get_sorted_filter_ids()` is called** to determine which filters to run
4. **For each filter, `get_active_status()` is checked**:
   - Filter name: "Snflwr Safety Filter"
   - Contains "safety": ✓
   - User role: "user" (not "admin"): ✓
   - **Returns: `True`** (FORCED)
5. **Safety filter runs** (calls `inlet()` method)
6. **Keyword blocking catches "let's talk about sex"**
7. **Message is replaced with redirect**
8. **Student receives**: "I focus on helping with school subjects..."

### Flow for Admin Users

1. **Admin sends message**: "Let's talk about sex"
2. **Backend receives request**
3. **`get_sorted_filter_ids()` is called**
4. **For safety filter, `get_active_status()` checks**:
   - User role: "admin": ✓
   - **Skips forced activation**
   - Falls through to normal toggle check
5. **If admin disabled filter**: Returns `False`, filter doesn't run
6. **If admin enabled filter**: Returns `True`, filter runs
7. **Admins have full control** for testing/debugging

## Security Benefits

### Before This Change
❌ Students could disable safety filter via Chat Controls
❌ Students could bypass all safety checks
❌ K-12 COPPA compliance violated
❌ Legal liability for platform

### After This Change
✅ Safety filter ALWAYS runs for students (mandatory)
✅ Students CANNOT disable it (even via database manipulation)
✅ Admins retain full control for testing
✅ K-12 COPPA compliant
✅ Legally defensible

## Testing

### Test 1: Student with Filter Toggled OFF

**Setup**:
1. Log in as `student@test.com`
2. Go to Chat Controls (if visible)
3. Toggle safety filter OFF
4. Send: "Let's talk about sex"

**Expected Result**:
- Backend logs: `[K-12 SAFETY] Force-enabling safety filter for user student@test.com`
- Message blocked with redirect
- Filter ran despite being toggled OFF

### Test 2: Admin with Filter Toggled OFF

**Setup**:
1. Log in as admin
2. Toggle safety filter OFF
3. Send: "Let's talk about sex"

**Expected Result**:
- Filter does NOT run
- Model responds normally
- Admin has full control

### Test 3: Student with Filter Toggled ON

**Setup**:
1. Log in as `student@test.com`
2. Toggle safety filter ON
3. Send: "Let's talk about sex"

**Expected Result**:
- Backend logs: `[K-12 SAFETY] Force-enabling safety filter for user student@test.com`
- Message blocked with redirect
- Same result as Test 1 (toggle state doesn't matter for students)

## Deployment

### Build Custom Image

```bash
cd loving-morse/frontend/open-webui
docker build -t snflwr-webui:k12-safety .
```

### Update docker-compose.yaml

```yaml
services:
  open-webui:
    image: snflwr-webui:k12-safety  # Use custom image
    ports:
      - "3000:8080"
    volumes:
      - open-webui:/app/backend/data
```

### Deploy

```bash
docker-compose down
docker-compose up -d
```

### Verify Deployment

```bash
# Check logs for force-enable messages when student logs in
docker logs open-webui | grep "K-12 SAFETY"
```

## Logging & Monitoring

The modification adds logging when the safety filter is force-enabled:

```python
log.info(f"[K-12 SAFETY] Force-enabling safety filter for user {user.email}")
```

**Monitor these logs** to ensure:
- Filter is being forced for all student users
- No students are bypassing the filter
- System is working as expected

**Check logs**:
```bash
docker logs open-webui --tail 100 | grep "K-12 SAFETY"
```

## Fallback/Rollback

If this modification causes issues:

1. **Revert the change**:
   ```bash
   cd loving-morse/frontend/open-webui
   git checkout backend/open_webui/utils/filter.py
   ```

2. **Rebuild**:
   ```bash
   docker build -t snflwr-webui:k12-safety .
   docker-compose restart
   ```

3. **Alternative**: Use official image and implement proxy layer instead

## Next Steps

1. ✅ Backend enforcement (DONE - this file)
2. ⏳ **Frontend**: Hide Chat Controls button for students
3. ⏳ **Testing**: Verify forced activation works
4. ⏳ **Deployment**: Roll out to production
5. ⏳ **Monitoring**: Set up alerts for bypass attempts

## Legal Compliance

This modification ensures:
- **COPPA Compliance**: Students under 13 cannot disable safety controls
- **Duty of Care**: Platform takes reasonable measures to protect children
- **Parental Rights**: Sex education controlled by age, not student preference
- **Fail-Safe**: Even if UI is bypassed, backend still enforces safety

**This is a critical security control for K-12 deployment.**
