<p align="center">
  <img src="assets/icon.png" alt="snflwr.ai" width="120" />
</p>

<h1 align="center">snflwr.ai</h1>

<p align="center">
  <strong>K-12 Safe AI Learning Platform</strong><br>
  Privacy-first AI tutoring with offline operation, backend-enforced child safety, and enterprise security.
</p>

<p align="center">
  <a href="#5-minute-setup">Setup</a>&nbsp;&bull;
  <a href="#safety">Safety</a>&nbsp;&bull;
  <a href="#deployment">Deployment</a>&nbsp;&bull;
  <a href="#configuration">Configuration</a>&nbsp;&bull;
  <a href="docs/guides/SETUP.md">Full Docs</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blue" alt="Version 1.0.0" />
  <img src="https://img.shields.io/badge/python-3.11-blue" alt="Python 3.11" />
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0 License" />
  <img src="https://img.shields.io/badge/tests-2200%2B-brightgreen" alt="2200+ Tests" />
  <img src="https://img.shields.io/badge/COPPA%2FFERPA-compliant-green" alt="COPPA/FERPA Compliant" />
</p>

---

## What is snflwr.ai?

snflwr.ai wraps [Open WebUI](https://github.com/open-webui/open-webui) with a FastAPI backend that enforces multi-layer content filtering, parental oversight, and encrypted data storage. Students interact with a polished chat interface; every message passes through a 5-stage safety pipeline that **cannot be bypassed** from the frontend.

It runs entirely on your hardware -- no cloud accounts, no data leaving your network.

| Audience | What they get |
|----------|---------------|
| **Parents & families** | Plug-and-play AI tutor on a USB drive. AES-256 encrypted, fully offline. |
| **Schools & districts** | PostgreSQL, Celery, Prometheus/Grafana, horizontal scaling, COPPA/FERPA audit trail. |
| **Developers** | FastAPI + Pydantic, 400+ pytest tests, typed config, structured logging with correlation IDs. |

---

## 5-Minute Setup

### Prerequisites

- **RAM:** 8 GB recommended (4 GB minimum with a smaller model)
- **Disk:** 10 GB free (OS + model + Docker images)
- **Docker Desktop:** Required (the installer will offer to install it for you)

### Install

```bash
# Linux / macOS
chmod +x setup.sh start_snflwr.sh   # make scripts executable (first time only)
./setup.sh

# Windows (Command Prompt)
.\setup.bat
```

The bootstrap script installs Python if needed, then launches the interactive installer which:

1. Creates a virtual environment
2. Installs all Python dependencies
3. Installs and configures Docker (if missing)
4. Installs Ollama and pulls an AI model sized to your hardware
5. Detects USB drives for offline/privacy mode
6. Configures the database (SQLite or PostgreSQL)
7. Generates all credentials and writes `.env`
8. Creates desktop shortcuts (Windows, macOS, Linux)
9. Validates the installation

### Start

```bash
# Linux / macOS
./start_snflwr.sh

# Windows
START_SNFLWR.bat          # Command Prompt (double-click)
.\start_snflwr.ps1        # PowerShell
```

> **Permission denied on Linux/macOS?** Run `chmod +x setup.sh start_snflwr.sh` first. This only needs to be done once.

Open **http://localhost:3000** -- the startup script launches the API server, Docker containers, and opens your browser automatically.

> **USB drive?** Double-click `Start Snflwr` in the USB root. Platform-specific launchers (`.bat`, `.desktop`, `.command`) detect your OS and launch the GUI or terminal script.

> **Already have Python and Ollama?** Run `python install.py` directly, then start.

<details>
<summary><strong>Windows PowerShell note</strong></summary>

If you see "running scripts is disabled", run once:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Or use `START_SNFLWR.bat` instead.
</details>

---

## Safety

snflwr.ai uses a multi-layer content filtering pipeline (input validation, normalization, pattern matching, optional LLM-based semantic classification, and age-adaptive rules for K-5 through 12th grade). The pipeline is **fail-closed** -- if any stage errors, content is blocked, not allowed through. Parents can monitor safety incidents, conversation history, and usage analytics in real time via the parent dashboard.

---

## Deployment

### Family / USB (SQLite)

Best for individual families, homeschools, and offline use. Data is stored locally with AES-256 encryption at rest.

```bash
./setup.sh && ./start_snflwr.sh
```

### Enterprise / School (PostgreSQL)

Best for school districts, multi-user deployments, and cloud hosting. Includes PostgreSQL, Redis, Celery, Prometheus, and Grafana.

```bash
python scripts/setup_production.py          # generate secrets + .env.production
enterprise/build.sh                         # build Docker images
docker compose -f docker/compose/docker-compose.yml up -d
```

See **[enterprise/README.md](enterprise/README.md)** for the full step-by-step guide.

---

## Configuration

The interactive installer generates a `.env` file with all required settings. Key variables:

```bash
# Database
DB_TYPE=sqlite                              # or: postgresql
SNFLWR_DATA_DIR=/path/to/data            # SQLite data directory

# Security
JWT_SECRET_KEY=<auto-generated>
DB_ENCRYPTION_ENABLED=true
DB_ENCRYPTION_KEY=<auto-generated>

# AI Model (set by installer based on hardware detection)
OLLAMA_DEFAULT_MODEL=qwen3.5:9b              # varies by system RAM
ENABLE_SAFETY_MODEL=false                   # set true for llama-guard3:1b classifier

# Infrastructure (enterprise only)
REDIS_ENABLED=false                         # true for enterprise
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DATABASE=snflwr_ai
```

All credentials are also saved to `CREDENTIALS.md` for safekeeping.

### AI Models

The installer detects your system RAM and recommends the best Qwen3.5 model:

| Model | Download | RAM | Best for |
|-------|----------|-----|----------|
| qwen3.5:0.8b | ~0.5 GB | 2 GB+ | Low-resource devices |
| qwen3.5:2b | ~1.3 GB | 4 GB+ | Older laptops |
| qwen3.5:4b | ~2.5 GB | 6 GB+ | Everyday use |
| qwen3.5:9b | ~5.5 GB | 8 GB+ | Mid-range systems (default) |
| qwen3.5:27b | ~16 GB | 24 GB+ | Higher quality |
| qwen3.5:35b | ~22 GB | 32 GB+ | Workstation / server |

To switch models after install:
```bash
ollama pull qwen3.5:4b
# Update OLLAMA_DEFAULT_MODEL in .env
```

---

## Database Encryption

SQLite databases are encrypted at rest using SQLCipher. Enable during install or manually:

```bash
export DB_ENCRYPTION_ENABLED=true
export DB_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

For existing databases, run the migration script:
```bash
python scripts/database/encrypt_database.py --source data/snflwr.db
```

See [DATABASE_ENCRYPTION.md](docs/guides/DATABASE_ENCRYPTION.md) for the complete guide.

---

## Security & Compliance

- **Encryption:** AES-256 at rest (SQLCipher), TLS 1.3 in transit
- **Authentication:** Argon2id with PBKDF2 fallback, JWT tokens
- **Privacy:** All data stored locally -- never sent to external APIs. USB deployment for complete physical data control.
- **COPPA:** Verifiable parental consent, data minimization, automated retention cleanup
- **FERPA:** Student records protected, parent/guardian access controls
- **GDPR:** Data deletion and export endpoints available

---

## Testing

```bash
pytest tests/ -v -m "not integration"
```

2200+ tests across 40+ test files covering authentication, profiles, safety pipeline, encryption, database operations, API routes, middleware, WebSockets, caching, error tracking, and model management.

---

## Monitoring (Enterprise)

Enterprise deployments include Grafana dashboards, Prometheus alerting, and Sentry error tracking with COPPA-compliant PII filtering. See [MONITORING_AND_ALERTS.md](docs/deployment/MONITORING_AND_ALERTS.md) for setup.

---

## Documentation

| Category | Guide |
|----------|-------|
| **Getting Started** | [SETUP.md](docs/guides/SETUP.md), [QUICKSTART.md](docs/guides/QUICKSTART.md) |
| **Admin** | [ADMIN_SETUP_GUIDE.md](docs/guides/ADMIN_SETUP_GUIDE.md) |
| **Security** | [DATABASE_ENCRYPTION.md](docs/guides/DATABASE_ENCRYPTION.md), [SECURITY_COMPLIANCE.md](docs/compliance/SECURITY_COMPLIANCE.md) |
| **Safety** | [BACKEND_SAFETY_ENFORCEMENT.md](docs/safety/BACKEND_SAFETY_ENFORCEMENT.md), [GRADE_BASED_FILTERING.md](docs/safety/GRADE_BASED_FILTERING.md) |
| **Compliance** | [COPPA_CONSENT_MECHANISM.md](docs/compliance/COPPA_CONSENT_MECHANISM.md), [AGE_16_POLICY.md](docs/compliance/AGE_16_POLICY.md) |
| **Deployment** | [PRODUCTION_DEPLOYMENT_GUIDE.md](docs/deployment/PRODUCTION_DEPLOYMENT_GUIDE.md), [USB_DEPLOYMENT_GUIDE.md](docs/deployment/USB_DEPLOYMENT_GUIDE.md) |
| **Architecture** | [ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md), [API_EXAMPLES.md](docs/architecture/API_EXAMPLES.md) |
| **Troubleshooting** | [TROUBLESHOOTING_GUIDE.md](docs/guides/TROUBLESHOOTING_GUIDE.md) |

---

## Contributing

Contributions welcome in these areas:

- Safety filter accuracy improvements
- Multi-language support
- Edge case testing
- Documentation
- UI/UX enhancements

Please open an issue or pull request on GitHub.

---

## Support

- **Issues:** [GitHub Issues](https://github.com/tmartin2113/snflwr-ai/issues)
- **Discussions:** [GitHub Discussions](https://github.com/tmartin2113/snflwr-ai/discussions)
- **Open WebUI Community:** [Discord](https://discord.gg/5rJgQTnV4s)

---

## License

**snflwr.ai** is licensed under the [GNU Affero General Public License v3.0](LICENSE). You can use, modify, and distribute it freely. If you run a modified version as a network service, you must share your source code under the same license.

The Open WebUI frontend is a forked component with its own license. See [frontend/open-webui/LICENSE](frontend/open-webui/LICENSE).

For commercial licensing inquiries (dual-licensing): licensing@snflwr.ai

---

## Acknowledgments

- [Open WebUI](https://github.com/open-webui/open-webui) -- Excellent open-source AI interface
- [Ollama](https://ollama.com) -- Local LLM inference made accessible
- [Qwen Team (Alibaba Cloud)](https://github.com/QwenLM) -- Qwen3.5 model family
- K-12 educators who provided feedback and testing

---

<p align="center">
  <strong>Built for educators, students, and families who value safety, privacy, and local AI.</strong>
</p>
