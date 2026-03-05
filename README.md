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
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0 License" />
  <img src="https://img.shields.io/badge/tests-2590%2B-brightgreen" alt="2590+ Tests" />
  <img src="https://img.shields.io/badge/coverage-86%25-brightgreen" alt="86% Test Coverage" />
  <img src="https://img.shields.io/badge/COPPA%2FFERPA-designed-green" alt="Designed for COPPA/FERPA compliance" />
</p>

---

## What is snflwr.ai?

snflwr.ai wraps [Open WebUI](https://github.com/open-webui/open-webui) with a FastAPI backend that enforces multi-layer content filtering, parental oversight, and encrypted data storage. Students interact with a polished chat interface; every message passes through a 5-stage safety pipeline that **cannot be bypassed** from the frontend.

It runs entirely on your hardware -- no cloud accounts, no data leaving your network.

| Audience | What they get |
|----------|---------------|
| **Parents & families** | Plug-and-play AI tutor on a USB drive. AES-256 encrypted, fully offline. |
| **Schools & districts** | PostgreSQL, Celery, Prometheus/Grafana, horizontal scaling, COPPA/FERPA audit trail. |
| **Developers** | FastAPI + Pydantic, 2,590+ pytest tests at 86% coverage, typed config, structured logging with correlation IDs. |

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

| I want to... | Command |
|---|---|
| Run from a USB drive, no internet needed | `./start_snflwr.sh` |
| Self-host on a home server or VPS | `./deploy.sh` |
| Deploy for a school or organization | `enterprise/build.sh` |

### Family / USB (no Docker required)

Best for individual families, homeschools, and offline use. Runs entirely from the USB — no Docker or internet connection needed.

```bash
./setup.sh && ./start_snflwr.sh
```

Data is stored locally with AES-256 encryption at rest. Double-click the platform launcher (`Start snflwr.bat` / `Start snflwr.command` / `Start snflwr.desktop`) for a GUI with service indicators.

### Home Server / Self-Hosted (Docker)

Best for home labs, VPS hosting, and anyone who wants a persistent always-on deployment. Requires Docker. Automatically detects your GPU.

```bash
./deploy.sh
```

That's it. `deploy.sh` handles secrets generation, GPU detection, image building, model download, and browser launch. On a headless server (no display) it skips the browser automatically.

```bash
./deploy.sh --stop      # stop all services
./deploy.sh --update    # pull latest updates
./deploy.sh --logs      # tail logs
./deploy.sh --model qwen3.5:4b   # use a smaller model (low-RAM machines)
```

### Enterprise / School (PostgreSQL + full stack)

Best for school districts, multi-user deployments, and cloud hosting. Includes PostgreSQL, Redis, Celery, Prometheus, and Grafana.

```bash
enterprise/build.sh                         # interactive setup: secrets, model, SSL
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
- **COPPA:** Built-in parental consent flow, data minimization, automated retention cleanup — designed to support COPPA compliance. Operators are responsible for verifying their specific deployment meets all applicable requirements.
- **FERPA:** Student record protections and parent/guardian access controls — designed to support FERPA compliance for school deployments.
- **GDPR:** Data deletion and export endpoints available to support GDPR rights obligations.

---

## Testing

```bash
pytest tests/ -v -m "not integration"
```

2,590+ tests across 71 test files at 86% coverage, covering authentication, profiles, safety pipeline, encryption, database operations, API routes, middleware, WebSockets, caching, error tracking, and model management.

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

Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), then open an issue or pull request on GitHub.

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
