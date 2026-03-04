# Open WebUI Frontend Analysis for snflwr.ai

> Comprehensive analysis of the Open WebUI fork and K-12 adaptation strategy

## Architecture Overview

**Framework**: SvelteKit 2.x + TypeScript + TailwindCSS
**Real-time**: Socket.io WebSocket connections
**Build**: Vite 5.x with static adapter (SPA mode)

```
/src
├── lib/
│   ├── apis/          # Backend API clients (organized by domain)
│   ├── components/    # Svelte components (admin, chat, layout, common)
│   ├── stores/        # Global state (user, config, settings)
│   └── utils/         # Helper functions
├── routes/
│   ├── (app)/         # Protected routes (chat, admin, workspace)
│   ├── auth/          # Login/signup
│   └── error/         # Error pages
```

## Key Existing Features We Can Leverage

### 1. User Management System
- **Roles**: admin, user, pending
- **Groups**: User groups with custom permissions
- **API**: `/src/lib/apis/users/index.ts` - Full CRUD operations

**For Snflwr**: Add `parent`, `student`, `teacher` roles

### 2. Permission System
Granular permissions already defined (`/src/lib/components/admin/Users/Groups/Permissions.svelte`):
- Workspace permissions (models, knowledge, prompts, tools)
- Chat permissions (file_upload, delete, share, export, controls)
- Feature permissions (api_keys, channels, web_search, code_interpreter)

**For Snflwr**: Create student permission preset that locks down:
- ❌ file_upload
- ❌ delete
- ❌ share/export
- ❌ code_interpreter
- ❌ workspace access
- ✅ web_search (filtered)

### 3. Admin Interface
Location: `/src/routes/(app)/admin/`

Existing features:
- User list with search, sort, pagination
- Settings panel with 12+ configuration sections
- Function management
- Analytics

**For Snflwr**: Add child profile management to admin panel

### 4. Real-time WebSocket
Socket.io integration in `/src/routes/+layout.svelte` (lines 97-177)

Events handled:
- `chat:completion` - Streaming AI responses
- `chat:title` - Auto-generated titles
- `execute:python` - Code execution

**For Snflwr**: Add safety monitoring events:
- `safety:alert` - Parent notifications
- `safety:violation` - Content filtering
- `activity:log` - Student activity tracking

## K-12 Adaptation Strategy

### Phase 1: Child Profile System

**New Stores** (`/src/lib/stores/index.ts`):
```typescript
export const childProfiles = writable([]);
export const activeChildProfile = writable(null);
```

**New Component** (`/src/lib/components/snflwr/ProfileSelector.svelte`):
- Shows after parent login
- Lists child profiles
- Selects active profile for session
- Stores in sessionStorage

**Backend API needed**:
```
GET  /api/v1/users/{userId}/children
POST /api/v1/users/{userId}/children
```

### Phase 2: Safety Monitoring

**Intercept at**:
1. **Message Input** - `/src/lib/components/chat/MessageInput.svelte`
   - Add safety check before sending
   - Block inappropriate content
   - Log all student messages

2. **Message Receiving** - WebSocket handler in `/src/routes/+layout.svelte`
   - Filter AI responses
   - Log for parental review
   - Detect patterns (repeated violations)

**New Utility** (`/src/lib/utils/safetyMiddleware.ts`):
```typescript
export async function validateOutgoingMessage(message, user) {
    // Call safety API
    // Log for students
    // Alert parent if needed
}
```

**Backend API needed**:
```
POST /api/v1/safety/check-message
POST /api/v1/safety/log-interaction
POST /api/v1/safety/alert-parent
```

### Phase 3: Parent Dashboard

**New Routes** (`/src/routes/(app)/parent/`):
```
/parent                 # Overview dashboard
/parent/children        # Child list
/parent/children/[id]   # Child detail
/parent/safety          # Safety alerts
/parent/settings        # Parent settings
```

**New Components** (`/src/lib/components/snflwr/ParentDashboard/`):
- `ChildActivityLog.svelte` - View chat history
- `SafetyAlerts.svelte` - Safety incidents with severity
- `UsageStats.svelte` - Time tracking, message counts
- `ProfileManager.svelte` - Manage child profiles

**Navigation Update** (`/src/lib/components/layout/Sidebar.svelte`):
```svelte
{#if $user?.role === 'parent'}
    <button on:click={() => goto('/parent')}>
        Parent Dashboard
    </button>
{/if}
```

**Backend API needed**:
```
GET /api/v1/activity/{childId}
GET /api/v1/safety/alerts/{parentId}
GET /api/v1/safety/reports/{childId}
```

### Phase 4: Student Restrictions

**Permission Preset** (`/src/lib/utils/studentRestrictions.ts`):
```typescript
export const STUDENT_PERMISSIONS = {
    workspace: { /* all false */ },
    chat: {
        file_upload: false,
        delete: false,
        share: false,
        export: false,
        multiple_models: false,
        controls: false
    },
    features: {
        api_keys: false,
        code_interpreter: false,
        direct_tool_servers: false
    }
};
```

**Apply on Login** (`/src/routes/(app)/+layout.svelte`):
```typescript
if (sessionUser.role === 'student') {
    sessionUser.permissions = STUDENT_PERMISSIONS;
}
```

**Hide UI Elements**: Add role checks throughout:
```svelte
{#if $user?.role !== 'student'}
    <!-- Advanced features -->
{/if}
```

## Critical File Locations

### Authentication Flow
```
/src/routes/auth/+page.svelte                    # Login page
/src/lib/apis/auths/index.ts                     # Auth API
/src/routes/+layout.svelte (lines 744-765)       # Auth check on mount
```

### User Management
```
/src/lib/components/admin/Users/UserList.svelte  # User list UI
/src/lib/apis/users/index.ts                     # User API
```

### Chat Interface
```
/src/lib/components/chat/Chat.svelte             # Main chat (69KB)
/src/lib/components/chat/MessageInput.svelte     # Message input
/src/routes/+layout.svelte (lines 325-480)       # WebSocket handler
```

### Permissions
```
/src/lib/components/admin/Users/Groups/Permissions.svelte  # Permission UI
/src/lib/stores/index.ts (SessionUser type)                # User type with permissions
```

## Integration Points Summary

| Feature | Frontend Location | Backend Needed |
|---------|------------------|----------------|
| Child Profiles | Profile selector after login | `/api/v1/users/{id}/children` |
| Safety Check | MessageInput.svelte submit handler | `/api/v1/safety/check-message` |
| Activity Log | Parent dashboard components | `/api/v1/activity/{childId}` |
| Safety Alerts | WebSocket + parent dashboard | `/api/v1/safety/alerts` |
| Student Permissions | Layout auth logic | Permission enforcement in backend |

## Next Steps

1. **Decide on approach**:
   - Option A: Build backend API first, then integrate
   - Option B: Mock APIs, build frontend, then wire up backend
   - Option C: Iterative - build API + frontend feature by feature

2. **Choose starting point**:
   - Child profile system (simplest)
   - Safety monitoring (most critical)
   - Parent dashboard (most visible)

3. **Backend architecture**:
   - FastAPI server with documented endpoints
   - PostgreSQL for production data
   - Redis for real-time features (alerts, activity streams)
   - WebSocket integration for live monitoring

## Technical Notes

- **Open WebUI uses Bearer tokens**: All API calls include `Authorization: Bearer {token}`
- **Local storage for session**: Token stored in `localStorage.token`
- **Svelte stores are reactive**: Changes automatically update UI
- **File-based routing**: Add routes by creating files in `/src/routes/`
- **Socket.io connection**: At `/ws/socket.io` endpoint

## References

- Open WebUI Docs: https://docs.openwebui.com
- SvelteKit: https://kit.svelte.dev
- Our existing safety filter: `/openwebui_safety_filter_age_adaptive.py` (works as Function plugin)
