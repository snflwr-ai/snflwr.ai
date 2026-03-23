---
---

# snflwr.ai - Production Readiness Roadmap

**Current Status:** Core architecture complete, components tested
**Branch:** claude/verify-cloud-migration-VBzMS
**Last Update:** 2025-12-20

---

## ✅ Completed

### Backend Foundation
- [x] Database schema (13 tables, age validation 0-18)
- [x] Configuration system (3-tier models, safety config)
- [x] FastAPI server structure
- [x] Profile manager (CRUD operations)
- [x] Authentication manager (structure)
- [x] Database initialization script
- [x] API route structure (profiles, chat, auth, safety, analytics)

### Frontend Components
- [x] ProfileSelector modal (Svelte)
- [x] CreateProfileModal form
- [x] ProfileIndicator sidebar widget
- [x] Snflwr API client (TypeScript)
- [x] Reactive stores (state management)
- [x] Layout integration (Open WebUI)

### Testing & Documentation
- [x] Age validation tests (8/8 passing)
- [x] API server tests (5/5 passing)
- [x] Test documentation
- [x] Setup documentation
- [x] .gitignore for production

---

## 🔴 Critical (Required for MVP)

### 1. Authentication System (Week 1)
**Priority:** CRITICAL
**Status:** Structure exists, needs integration

**Tasks:**
- [ ] Complete parent registration flow
  - Frontend form in Open WebUI
  - Backend endpoint working
  - Password hashing (SHA-256 with salt)
  - Email validation

- [ ] Implement login system
  - Session token generation
  - JWT or session-based auth
  - Token storage (localStorage/cookies)
  - Token refresh mechanism

- [ ] Add authentication middleware
  - Protect all API routes
  - Verify tokens on requests
  - Handle expired sessions

- [ ] Test authentication flow
  - Registration → Login → Protected routes
  - Session persistence
  - Logout functionality

**Files to work on:**
- `api/routes/auth.py` (endpoints)
- `core/authentication.py` (backend logic)
- Frontend: Login/Register components
- Middleware for route protection

---

### 2. Profile Management Integration (Week 1-2)
**Priority:** CRITICAL
**Status:** Backend ready, frontend needs connection

**Tasks:**
- [ ] Connect frontend to backend API
  - API client already created ✓
  - Test API calls from frontend
  - Handle errors gracefully

- [ ] Complete profile CRUD flow
  - Create: Form → API → Database ✓
  - Read: Load profiles on login ✓
  - Update: Edit profile functionality
  - Delete/Deactivate: Remove profiles

- [ ] Session persistence
  - Save active profile to sessionStorage ✓
  - Restore on page reload
  - Clear on logout

- [ ] Profile switching
  - Sidebar indicator working ✓
  - Click to change profiles
  - Update chat context

**Files to work on:**
- `frontend/open-webui/src/lib/apis/snflwr/index.ts` ✓
- `frontend/open-webui/src/lib/stores/snflwr.ts` ✓
- `api/routes/profiles.py` (test endpoints)

---

### 3. Ollama Integration (Week 2)
**Priority:** CRITICAL
**Status:** Config ready, needs implementation

**Tasks:**
- [ ] Install and configure Ollama
  - Install Ollama on server/dev machine
  - Download models (chosen based on available hardware):
    - `ollama pull qwen3.5:9b` (default)
    - `ollama pull qwen3.5:27b` (if hardware supports it)

- [ ] Test Ollama client
  - `utils/ollama_client.py` exists ✓
  - Test generation with each model
  - Verify context windows (16K, 32K, 128K)

- [ ] Integrate with chat endpoint
  - Route requests to correct model based on tier
  - Handle streaming responses
  - Error handling (timeouts, model unavailable)

- [ ] Load testing
  - Concurrent requests
  - Memory usage monitoring
  - Response time benchmarks

**Files to work on:**
- `utils/ollama_client.py` ✓
- `api/routes/chat.py` (integrate generation)
- Test script for Ollama

---

### 4. Safety Filter Pipeline (Week 2-3)
**Priority:** CRITICAL
**Status:** Architecture designed, needs implementation

**Tasks:**
- [ ] Layer 1: Keyword Filter
  - Implement keyword matching (config.py has list ✓)
  - Age-adaptive filtering (K-5, 6-8, 9-12)
  - Return safe/unsafe + reason

- [ ] Layer 2: LLM Classifier
  - Use lightweight model (llama-guard or custom)
  - Classify: safe/educational/inappropriate
  - Log classification scores

- [ ] Layer 3: Generation (Ollama)
  - Already covered in Ollama integration

- [ ] Layer 4: Response Validation
  - Check generated response for safety
  - Block if inappropriate content detected
  - Log all incidents

- [ ] Incident Logging
  - Save to `safety_incidents` table
  - Track severity (minor/major/critical)
  - Parent notification triggers

- [ ] Testing
  - Test cases for each layer
  - Bypass prevention (can students disable?)
  - Performance benchmarks

**Files to work on:**
- `safety/pipeline.py` (5-stage safety pipeline: validate → normalize → pattern → classify → age-gate)
- `safety/incident_logger.py` (logging)
- `api/routes/chat.py` (integrate pipeline)
- `test_safety.sh` ✓

---

### 5. End-to-End Testing (Week 3)
**Priority:** CRITICAL
**Status:** Unit tests done, need integration tests

**Tasks:**
- [ ] Manual testing checklist
  - [ ] Parent registration
  - [ ] Parent login
  - [ ] Create child profile (age 0-18)
  - [ ] Reject invalid age (19+, negative)
  - [ ] Profile selection modal
  - [ ] Profile switching
  - [ ] Chat with safety filter
  - [ ] View safety incidents

- [ ] Automated integration tests
  - API endpoint tests
  - Frontend component tests
  - Safety pipeline tests

- [ ] Security testing
  - SQL injection prevention
  - XSS prevention
  - Auth bypass attempts
  - Rate limiting

- [ ] Performance testing
  - Load test (100+ concurrent users)
  - Response time benchmarks
  - Memory leak detection

**Files to create:**
- `tests/integration/` directory
- `tests/security/` directory
- `tests/performance/` directory

---

## 🟡 Important (Should-Have for Production)

### 6. Parent Dashboard (Week 3-4)
**Priority:** HIGH
**Status:** Not started

**Tasks:**
- [ ] Dashboard page in Open WebUI
  - Overview of all children
  - Usage statistics
  - Recent activity

- [ ] Safety monitoring
  - List of safety incidents
  - Severity indicators
  - Acknowledge/resolve incidents

- [ ] Analytics
  - Sessions per child
  - Topics discussed
  - Time spent
  - Weekly/monthly trends

- [ ] Alerts management
  - View unacknowledged alerts
  - Email notification settings
  - Alert thresholds

**Files to create:**
- `frontend/open-webui/src/routes/(app)/dashboard/+page.svelte`
- `frontend/open-webui/src/lib/components/dashboard/`
- `api/routes/analytics.py` (already exists ✓)

---

### 7. Deployment Configuration (Week 4)
**Priority:** HIGH
**Status:** Local dev only

**Tasks:**
- [ ] Environment configuration
  - Create `.env.example`
  - Document all environment variables
  - Production vs development configs

- [ ] Database migrations
  - Version control for schema changes
  - Migration scripts
  - Backup strategy

- [ ] Docker setup (optional but recommended)
  - Dockerfile for API
  - Dockerfile for Ollama + models
  - docker-compose.yml for full stack
  - Volume mounts for data persistence

- [ ] Deployment guide
  - Server requirements (RAM, CPU, GPU)
  - Installation steps
  - SSL/TLS setup
  - Reverse proxy (nginx)

- [ ] Monitoring
  - Application logs
  - Error tracking (Sentry?)
  - Performance monitoring
  - Uptime monitoring

**Files to create:**
- `.env.example`
- `docker-compose.yml`
- `docs/DEPLOYMENT.md`
- `scripts/backup_db.sh`
- `scripts/migrate_db.sh`

---

### 8. Documentation (Week 4)
**Priority:** MEDIUM-HIGH
**Status:** Technical docs exist, need user docs

**Tasks:**
- [ ] User documentation
  - Parent guide (how to create accounts, profiles)
  - Understanding safety features
  - Viewing analytics
  - Managing alerts

- [ ] Administrator guide
  - Installation
  - Configuration
  - Model management
  - Safety tuning
  - Backup/restore

- [ ] API documentation
  - OpenAPI/Swagger for FastAPI
  - Endpoint descriptions
  - Request/response examples

- [ ] Development guide
  - Architecture overview
  - How to contribute
  - Testing guidelines
  - Code style

**Files to create:**
- `docs/USER_GUIDE.md`
- `docs/ADMIN_GUIDE.md`
- `docs/API_DOCS.md` (auto-generated from FastAPI)
- `docs/CONTRIBUTING.md`

---

## 🟢 Nice-to-Have (Future Enhancements)

### 9. WebSocket Real-time Features
**Priority:** LOW
**Status:** Not started

**Tasks:**
- [ ] WebSocket server setup
- [ ] Real-time safety alerts
- [ ] Live chat monitoring
- [ ] Session presence indicators

---

### 10. Email Notifications
**Priority:** LOW
**Status:** Config exists, not implemented

**Tasks:**
- [ ] SMTP configuration
- [ ] Email templates
- [ ] Alert emails for critical incidents
- [ ] Weekly summary emails

---

### 11. Advanced Analytics
**Priority:** LOW
**Status:** Database tables exist

**Tasks:**
- [ ] Learning progress tracking
- [ ] Subject area analysis
- [ ] Engagement metrics
- [ ] Export reports (PDF/CSV)

---

### 12. Mobile Optimization
**Priority:** LOW
**Status:** Not started

**Tasks:**
- [ ] Responsive design improvements
- [ ] Touch-friendly controls
- [ ] Mobile app (optional)

---

## Timeline Estimate

### Week 1: Authentication & Core Flow
- Day 1-2: Parent registration/login
- Day 3-4: Profile management integration
- Day 5: Testing & bug fixes

### Week 2: AI Integration
- Day 1-2: Ollama setup and integration
- Day 3-5: Safety filter implementation

### Week 3: Testing & Dashboard
- Day 1-3: End-to-end testing
- Day 4-5: Parent dashboard basics

### Week 4: Production Prep
- Day 1-2: Deployment configuration
- Day 3-4: Documentation
- Day 5: Final testing & launch prep

**Estimated Time to MVP:** 3-4 weeks of focused development

---

## Critical Path (Must Complete in Order)

1. **Authentication** → Can't do anything without users
2. **Profile Management** → Need to select child profiles
3. **Ollama Integration** → Need AI to work
4. **Safety Filter** → Core value proposition
5. **Testing** → Ensure it works
6. **Deployment** → Make it accessible

Everything else can be done in parallel or after MVP.

---

## Immediate Next Steps (This Week)

### Priority 1: Authentication
```bash
# Backend
1. Test core/authentication.py registration
2. Create login endpoint in api/routes/auth.py
3. Add session management

# Frontend
4. Create Login.svelte component
5. Create Register.svelte component
6. Add auth state to stores
```

### Priority 2: Profile Connection
```bash
# Test existing API
1. Start FastAPI server: uvicorn api.server:app --reload
2. Test profile creation: curl -X POST http://localhost:8000/api/profiles/
3. Verify database entries

# Frontend testing
4. Create test parent account
5. Test profile selector modal
6. Test profile creation form
```

### Priority 3: Ollama Setup
```bash
1. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh
2. Pull models: ollama pull qwen3.5:9b
3. Test generation: ollama run qwen3.5:9b "Hello, how are you?"
4. Test utils/ollama_client.py
```

---

## Risk Assessment

### High Risk
- **Ollama RAM Requirements:** 14B model needs 16GB+ RAM
  - Mitigation: Start with 3B/7B models, upgrade hardware later

- **Safety Filter Bypasses:** Students might try to circumvent
  - Mitigation: Backend enforcement, no client-side overrides

- **Scale:** Multiple concurrent users with LLM inference
  - Mitigation: Start small, add rate limiting, queue system

### Medium Risk
- **Model Performance:** Qwen3 might not be ideal for K-12
  - Mitigation: Test thoroughly, consider Llama-3 alternatives

- **Session Management:** Complex with parent/child switching
  - Mitigation: Thorough testing, clear session boundaries

### Low Risk
- **Database Scaling:** SQLite might not scale
  - Mitigation: Easy migration to PostgreSQL (schema compatible)

---

## Success Metrics

### MVP Launch Criteria
- [ ] 10 parent accounts created
- [ ] 20+ child profiles
- [ ] 100+ safe conversations
- [ ] 0 critical safety incidents missed
- [ ] < 2s response time (95th percentile)
- [ ] < 1% error rate
- [ ] Parent dashboard functional
- [ ] Documentation complete

### Post-Launch (Month 1)
- [ ] 100 active families
- [ ] 500+ child profiles
- [ ] 10,000+ conversations
- [ ] 99.9% safety incident detection
- [ ] < 3s response time
- [ ] Parent satisfaction > 4/5

---

## Resources Needed

### Development
- 1 Backend developer (Python/FastAPI)
- 1 Frontend developer (Svelte/TypeScript)
- 1 DevOps engineer (deployment)
- QA testing resources

### Infrastructure
- **Development:**
  - 16GB RAM (for testing all models)
  - 50GB storage

- **Production (initial):**
  - Server: 32GB RAM, 8 cores
  - Storage: 100GB SSD
  - GPU: Optional (for 14B model)
  - Bandwidth: 1TB/month

### Budget Estimate
- Hosting: $100-200/month
- Domain/SSL: $20/year
- Email service: $10/month
- Monitoring: $20/month
- **Total:** ~$150-250/month

---

## Questions to Answer

1. **Hosting:** Where will this be deployed? (AWS, GCP, self-hosted?)
2. **Scale:** How many users expected in first 3 months?
3. **Support:** Who handles parent support requests?
4. **Legal:** Privacy policy for K-12 data? COPPA compliance?
5. **Pricing:** Free tier limits? Paid subscriptions?
6. **Hardware:** Budget for GPU acceleration?

---

## Summary

**Ready to Start:** ✅ Backend structure, frontend components
**Next Focus:** Authentication → Profile Integration → Ollama → Safety
**Estimated Timeline:** 3-4 weeks to production-ready MVP
**Key Blocker:** Need to implement authentication before anything else works end-to-end

The architecture is solid. The main work now is connecting the pieces and implementing the safety layer. The profile selector and database are production-ready!
