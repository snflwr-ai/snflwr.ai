# snflwr.ai Frontend

This directory contains the frontend UI for snflwr.ai, based on Open WebUI.

## Structure

```
frontend/
├── open-webui/          # Forked from https://github.com/open-webui/open-webui
│   ├── backend/         # Open WebUI's Python backend (FastAPI)
│   └── src/             # Svelte frontend
└── snflwr-bridge/    # Custom integration layer (to be created)
    ├── safety_middleware.py
    ├── auth_middleware.py
    └── educator_routes.py
```

## Open WebUI Version

- **Forked from**: open-webui/open-webui
- **Latest commit**: 6f1486ffd (Dec 16, 2024)
- **Version**: 0.6.41

## Integration Architecture

```
User Browser
    ↓
Open WebUI Frontend (Svelte)
    ↓
Open WebUI Backend (FastAPI) ← Modified with Snflwr middleware
    ↓
Snflwr Safety Pipeline (5 stages)
    ↓
Ollama (Local AI)
```

## Customizations Needed

### 1. Safety Pipeline Integration
Inject our 5-stage safety pipeline into Open WebUI's chat endpoint:
- Stage 1: Input validation (length, encoding, injection detection)
- Stage 2: Normalization (unicode, whitespace, obfuscation removal)
- Stage 3: Pattern matching (keyword-based blocking)
- Stage 4: Semantic classification (LLM-based safety check)
- Stage 5: Age gate (grade-level content enforcement)

### 2. Authentication Extension
Extend Open WebUI's auth to support:
- Parent accounts with subscription tiers
- Child profiles (multiple per parent)
- Age-based model selection

### 3. Freemium Limits
Add conversation count tracking:
- FREE: 10 conversations/week
- STANDARD: 50 conversations/week
- PREMIUM/GIFTED: Unlimited

### 4. Parent Dashboard
Add new routes for parents:
- `/dashboard/safety` - View safety incidents
- `/dashboard/progress` - Child progress analytics
- `/dashboard/transcripts` - Conversation logs
- Uses educator_engine.py for AI-powered insights

### 5. Branding
- Replace "Open WebUI" with "snflwr.ai"
- Custom color scheme (snflwr yellow/green)
- K-12 friendly UI elements
- Remove enterprise features (teams, workspaces)

## Development Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- Ollama installed locally

### Running Open WebUI (Stock)
```bash
cd open-webui
pip install -r backend/requirements.txt
cd backend
uvicorn main:app --reload --port 8080
```

### Running with Snflwr Integration (To Be Implemented)
```bash
# From repo root
python -m api.server
```

## Environment Variables

Create `.env` in open-webui directory:
```env
OLLAMA_BASE_URL=http://localhost:11434
WEBUI_SECRET_KEY=your-secret-key-here

# Snflwr-specific
SNFLWR_BACKEND_URL=http://localhost:8000
SAFETY_PIPELINE_ENABLED=true
FREEMIUM_ENABLED=true
```

## Next Steps

1. ✅ Fork Open WebUI (DONE)
2. ⏳ Create API integration layer in ../api/
3. ⏳ Modify Open WebUI backend to call Snflwr API
4. ⏳ Add parent dashboard routes
5. ⏳ Customize branding and UI
6. ⏳ Add subscription/freemium logic
7. ⏳ Deploy together

## File Organization

Keep Snflwr-specific code separate from Open WebUI:
- ✅ Don't modify Open WebUI core files directly
- ✅ Use middleware/plugins where possible
- ✅ Document all customizations
- ✅ Makes pulling updates easier

## License

- Open WebUI: MIT License
- snflwr.ai modifications: (Your license)
