#!/usr/bin/env python3
"""
USB Image Builder for snflwr.ai
Creates a self-contained, distributable USB image with pre-configured SQLite

Usage:
    python scripts/build_usb_image.py [output_dir]

Output:
    - Creates a ready-to-use USB directory structure
    - Pre-initializes SQLite database with schema
    - Includes launcher scripts for all platforms
    - Packages as .zip for easy distribution
"""

import os
import sys
import shutil
import sqlite3
import secrets
import zipfile
from pathlib import Path
from datetime import datetime, timezone

def print_step(msg):
    print(f"  >> {msg}")

def print_success(msg):
    print(f"  [OK] {msg}")

def print_warning(msg):
    print(f"  [WARN] {msg}")

def print_error(msg):
    print(f"  [ERROR] {msg}")

def _mask_secret(value: str, visible: int = 4) -> str:
    """Show only last N chars of a secret for verification."""
    s = str(value)
    if len(s) <= visible:
        return '***'
    return f"***{s[-visible:]}"


class USBImageBuilder:
    """Build a distributable USB image for snflwr.ai"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.project_root = Path(__file__).parent.parent
        self.usb_root = output_dir / "SnflwrAI"

    def build(self):
        """Execute the full build process"""
        print_step("Building snflwr.ai USB Image")
        print(f"Output directory: {self.output_dir}\n")

        # Create directory structure
        self.create_directory_structure()

        # Copy application files
        self.copy_application_files()

        # Initialize database
        self.initialize_database()

        # Create configuration files
        self.create_config_files()

        # Create launcher scripts
        self.create_launcher_scripts()

        # Create documentation
        self.create_documentation()

        # Package as ZIP
        zip_path = self.create_zip_package()

        print_success(f"\n[OK] USB image built successfully!")
        print(f"\nUSB Image Location: {self.usb_root}")
        print(f"ZIP Package: {zip_path}")
        print(f"\nTo deploy:")
        print(f"  1. Extract {zip_path.name} to a USB drive")
        print(f"  2. Run launcher script (run_snflwr.bat or run_snflwr.sh)")
        print(f"  3. Access at http://localhost:8000")

    def create_directory_structure(self):
        """Create USB directory structure"""
        print_step("Creating directory structure...")

        # Clean and create root
        if self.usb_root.exists():
            shutil.rmtree(self.usb_root)
        self.usb_root.mkdir(parents=True)

        # Create subdirectories
        dirs = [
            'data',              # Database and encryption keys
            'logs',              # Application logs
            'backups',           # Database backups
            'app',               # Application code
            'app/api',
            'app/core',
            'app/storage',
            'app/safety',
            'app/utils',
            'app/models',
            'docs',              # Documentation
        ]

        for dir_path in dirs:
            (self.usb_root / dir_path).mkdir(parents=True, exist_ok=True)
            print_success(f"Created {dir_path}/")

        # Create __init__.py for app package so Python can find it
        init_path = self.usb_root / 'app' / '__init__.py'
        init_path.write_text('# snflwr.ai USB Package\n')
        print_success("Created app/__init__.py")

    def copy_application_files(self):
        """Copy application code to USB image"""
        print_step("Copying application files...")

        # Directories to copy
        app_dirs = [
            'api',
            'core',
            'storage',
            'safety',
            'utils',
            'models',
        ]

        for dir_name in app_dirs:
            src = self.project_root / dir_name
            dst = self.usb_root / 'app' / dir_name

            if src.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                print_success(f"Copied {dir_name}/")

        # Copy essential files
        essential_files = [
            'config.py',
            ('requirements-usb.txt', 'requirements.txt'),  # Use USB requirements, rename to requirements.txt
        ]

        for file_info in essential_files:
            if isinstance(file_info, tuple):
                src_name, dst_name = file_info
            else:
                src_name = dst_name = file_info

            src = self.project_root / src_name
            dst = self.usb_root / 'app' / dst_name

            if src.exists():
                shutil.copy2(src, dst)
                print_success(f"Copied {src_name} -> {dst_name}")

        # Copy database schema
        schema_src = self.project_root / 'database' / 'schema.sql'
        schema_dst = self.usb_root / 'app' / 'schema.sql'
        if schema_src.exists():
            shutil.copy2(schema_src, schema_dst)
            print_success("Copied database schema")

    def initialize_database(self):
        """Initialize SQLite database with schema"""
        print_step("Initializing database...")

        db_path = self.usb_root / 'data' / 'snflwr.db'
        schema_path = self.project_root / 'database' / 'schema.sql'

        # Read schema
        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        # Create database and execute schema
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Execute schema (split on semicolons for multiple statements)
        for statement in schema_sql.split(';'):
            if statement.strip():
                try:
                    cursor.execute(statement)
                except sqlite3.Error as e:
                    print_warning(f"Schema statement warning: {e}")

        conn.commit()

        # Enable WAL mode for better concurrency
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")

        conn.commit()
        conn.close()

        print_success(f"Database initialized at data/snflwr.db")
        print_success(f"Database size: {db_path.stat().st_size / 1024:.1f} KB")

    def create_config_files(self):
        """Create configuration files"""
        print_step("Creating configuration files...")

        # Generate secure secrets
        jwt_secret = secrets.token_hex(32)
        dashboard_password = secrets.token_hex(16)

        # Create .env template
        env_content = f"""# snflwr.ai USB Deployment Configuration
# Generated: {datetime.now(timezone.utc).isoformat()}

# Data directory - keeps all data on the USB drive
SNFLWR_DATA_DIR=./data

# Database Configuration (SQLite - Privacy First)
DATABASE_TYPE=sqlite
DATABASE_PATH=./data/snflwr.db
ENCRYPTION_KEY_PATH=./data
LOG_PATH=./logs

# Security Configuration
JWT_SECRET_KEY={jwt_secret}
PARENT_DASHBOARD_PASSWORD={dashboard_password}

# Disable services not available on USB
REDIS_ENABLED=false
CELERY_ENABLED=false
SENTRY_ENABLED=false
PROMETHEUS_ENABLED=false

# Application Settings
ENVIRONMENT=production
LOG_LEVEL=INFO

# IMPORTANT: Save this dashboard password!
# Parent Dashboard: http://localhost:8000/dashboard
# Password: {dashboard_password}
"""

        env_path = self.usb_root / '.env'
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)
        os.chmod(str(env_path), 0o600)  # Restrict to owner-only access

        print_success("Created .env configuration")
        print_warning(f"Dashboard password: {_mask_secret(dashboard_password)} (see DASHBOARD_PASSWORD.txt)")

        # Save password to separate file for reference
        password_file = self.usb_root / 'DASHBOARD_PASSWORD.txt'
        with open(password_file, 'w', encoding='utf-8') as f:
            f.write(f"snflwr.ai Parent Dashboard Password\n")
            f.write(f"========================================\n\n")
            f.write(f"Password: {dashboard_password}\n\n")
            f.write(f"URL: http://localhost:8000/dashboard\n\n")
            f.write(f"WARNING: KEEP THIS FILE SECURE - It contains access to child safety data!\n")
        os.chmod(str(password_file), 0o600)  # Restrict to owner-only access

        print_success("Created DASHBOARD_PASSWORD.txt")

    def create_launcher_scripts(self):
        """Create platform-specific launcher scripts"""
        print_step("Creating launcher scripts...")

        # Windows launcher
        windows_launcher = self.usb_root / 'run_snflwr.bat'
        with open(windows_launcher, 'w', encoding='utf-8') as f:
            f.write("""@echo off
setlocal enabledelayedexpansion
REM snflwr.ai USB Launcher for Windows
REM Fully automated setup - installs everything needed

echo.
echo ========================================
echo   snflwr.ai - K-12 Safe AI Learning
echo ========================================
echo.

REM Set working directory to USB root FIRST
cd /d "%~dp0"

REM ==========================================
REM STEP 1: Check/Install Python
REM ==========================================
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed.
    echo.
    echo Opening Python download page...
    echo Please install Python 3.10+ and CHECK "Add Python to PATH"
    echo Then run this script again.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python found

REM ==========================================
REM STEP 2: Check/Install Ollama (AI Engine)
REM ==========================================
ollama --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo Ollama AI engine is not installed.
    echo Downloading Ollama installer...
    echo.
    curl -L -o "%TEMP%\\OllamaSetup.exe" "https://ollama.com/download/OllamaSetup.exe"
    if errorlevel 1 (
        echo.
        echo Could not download Ollama automatically.
        echo Please install manually from: https://ollama.com/download
        start https://ollama.com/download
        pause
        exit /b 1
    )
    echo Installing Ollama...
    start /wait "" "%TEMP%\\OllamaSetup.exe" /VERYSILENT /NORESTART
    del "%TEMP%\\OllamaSetup.exe" 2>nul
    echo.
    echo Ollama installed. You may need to restart this script
    echo if Ollama was not added to your PATH yet.
    echo.
    REM Refresh PATH by reading from registry
    for /f "tokens=2*" %%a in ('reg query "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment" /v Path 2^>nul') do set "PATH=%%b;%PATH%"
    for /f "tokens=2*" %%a in ('reg query "HKCU\\Environment" /v Path 2^>nul') do set "PATH=%%b;%PATH%"
    REM Verify ollama is now available
    ollama --version >nul 2>&1
    if errorlevel 1 (
        echo Ollama installed but not found on PATH.
        echo Please close this window and run the script again.
        pause
        exit /b 1
    )
)
echo [OK] Ollama found

REM Make sure Ollama is running
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
if errorlevel 1 (
    echo Starting Ollama service...
    start "" ollama serve
    timeout /t 10 /nobreak >nul
)

REM ==========================================
REM STEP 3: Detect hardware and pull AI model
REM ==========================================
echo Detecting hardware...
if "%OLLAMA_DEFAULT_MODEL%"=="" (
    for /f %%G in ('powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)"') do set "RAM_GB=%%G"
    if !RAM_GB! GEQ 32 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:35b
    ) else if !RAM_GB! GEQ 24 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:27b
    ) else if !RAM_GB! GEQ 8 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:9b
    ) else if !RAM_GB! GEQ 6 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:4b
    ) else if !RAM_GB! GEQ 4 ( set OLLAMA_DEFAULT_MODEL=qwen3.5:2b
    ) else ( set OLLAMA_DEFAULT_MODEL=qwen3.5:0.8b )
    echo Detected !RAM_GB! GB RAM -- using !OLLAMA_DEFAULT_MODEL!
)
echo Checking AI model...
ollama list 2>nul | find "%OLLAMA_DEFAULT_MODEL%" >nul
if errorlevel 1 (
    echo.
    echo Downloading AI model %OLLAMA_DEFAULT_MODEL%...
    echo This only happens once. Please wait...
    echo.
    ollama pull %OLLAMA_DEFAULT_MODEL%
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to download AI model.
        echo Please check your internet connection and try again.
        pause
        exit /b 1
    )
)
echo [OK] AI model ready (%OLLAMA_DEFAULT_MODEL%)

REM ==========================================
REM STEP 4: Install Python dependencies
REM ==========================================
echo Checking dependencies...
python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo Installing Python dependencies...
    echo.
    python -m pip install --only-binary :all: -r app/requirements.txt
    if errorlevel 1 (
        python -m pip install -r app/requirements.txt
        if errorlevel 1 (
            echo.
            echo ERROR: Failed to install dependencies
            pause
            exit /b 1
        )
    )
)
echo [OK] Dependencies installed

REM ==========================================
REM STEP 5: Launch snflwr.ai
REM ==========================================
echo.
echo ========================================
echo   All systems ready!
echo ========================================
echo.
echo   App:       http://localhost:8000
echo   Dashboard: http://localhost:8000/dashboard
echo   AI Model:  %OLLAMA_DEFAULT_MODEL%
echo.
echo   Press Ctrl+C to stop the server
echo ========================================
echo.

python -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000

echo.
echo ========================================
echo Server stopped or encountered an error.
echo ========================================
pause
""")

        print_success("Created run_snflwr.bat (Windows)")

        # Unix launcher (macOS/Linux)
        unix_launcher = self.usb_root / 'run_snflwr.sh'
        with open(unix_launcher, 'w', encoding='utf-8') as f:
            f.write("""#!/bin/bash
# snflwr.ai USB Launcher for macOS/Linux
# Auto-installs prerequisites and launches the application

# Set working directory to script location (USB root)
cd "$(dirname "$0")"

echo ""
echo "========================================"
echo "  snflwr.ai - K-12 Safe AI Learning"
echo "  Plug-and-Play USB Edition"
echo "========================================"
echo ""

# ========================================
# STEP 1: Check/Install Python 3
# ========================================
echo "[Step 1/5] Checking Python 3..."

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo "  Found: $PYTHON_VERSION"
else
    echo "  Python 3 not found. Attempting to install..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS - check for Homebrew first
        if command -v brew &> /dev/null; then
            echo "  Installing Python via Homebrew..."
            brew install python3
        else
            echo ""
            echo "  ERROR: Python 3 is required but not installed."
            echo "  Please install Python 3 from: https://www.python.org/downloads/"
            echo "  Or install Homebrew first: /bin/bash -c \\\"\\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\\\""
            echo ""
            read -p "  Press Enter to exit..."
            exit 1
        fi
    else
        # Linux - try apt or yum
        if command -v apt-get &> /dev/null; then
            echo "  Installing Python via apt (may require sudo)..."
            sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
        elif command -v yum &> /dev/null; then
            echo "  Installing Python via yum (may require sudo)..."
            sudo yum install -y python3 python3-pip
        elif command -v dnf &> /dev/null; then
            echo "  Installing Python via dnf (may require sudo)..."
            sudo dnf install -y python3 python3-pip
        else
            echo ""
            echo "  ERROR: Python 3 is required but not installed."
            echo "  Please install Python 3 from: https://www.python.org/downloads/"
            echo ""
            read -p "  Press Enter to exit..."
            exit 1
        fi
    fi

    # Verify installation
    if ! command -v python3 &> /dev/null; then
        echo "  ERROR: Python installation failed."
        echo "  Please install manually from: https://www.python.org/downloads/"
        read -p "  Press Enter to exit..."
        exit 1
    fi
    echo "  Python installed successfully!"
fi

# ========================================
# STEP 2: Check/Install Ollama
# ========================================
echo "[Step 2/5] Checking Ollama..."

if command -v ollama &> /dev/null; then
    echo "  Found: Ollama $(ollama --version 2>&1 | head -1)"
else
    echo "  Ollama not found. Installing..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS - use Homebrew if available, otherwise direct download
        if command -v brew &> /dev/null; then
            echo "  Installing Ollama via Homebrew..."
            brew install ollama
        else
            echo "  Please install Ollama from: https://ollama.com/download"
            echo "  Download the macOS app and drag it to Applications."
            echo "  Then run this script again."
            echo ""
            open "https://ollama.com/download" 2>/dev/null
            read -p "  Press Enter to exit..."
            exit 1
        fi
    else
        # Linux - use official install script
        echo "  Running Ollama install script..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi

    # Verify installation
    if ! command -v ollama &> /dev/null; then
        echo ""
        echo "  ERROR: Ollama installation failed."
        echo "  Please install manually from: https://ollama.com/download"
        echo ""
        read -p "  Press Enter to exit..."
        exit 1
    fi
    echo "  Ollama installed successfully!"
fi

# Make sure Ollama is running
echo "  Starting Ollama service..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS - case-insensitive process check
    pgrep -iq "ollama" || (open -a Ollama 2>/dev/null || ollama serve > /tmp/ollama.log 2>&1 &)
else
    # Linux - start with systemctl or directly
    pgrep -ix "ollama" > /dev/null || (systemctl start ollama 2>/dev/null || ollama serve > /tmp/ollama.log 2>&1 &)
fi
sleep 5

# ========================================
# STEP 3: Detect hardware and pull AI model
# ========================================
if [ -z "$OLLAMA_DEFAULT_MODEL" ]; then
    if [ -f /proc/meminfo ]; then
        ram_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
        ram_gb=$(( (ram_kb + 524288) / 1024 / 1024 ))
    elif command -v sysctl >/dev/null 2>&1; then
        ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
        ram_gb=$(( (ram_bytes + 536870912) / 1024 / 1024 / 1024 ))
    else
        ram_gb=8
    fi
    if [ "$ram_gb" -ge 32 ]; then OLLAMA_DEFAULT_MODEL="qwen3.5:35b"
    elif [ "$ram_gb" -ge 24 ]; then OLLAMA_DEFAULT_MODEL="qwen3.5:27b"
    elif [ "$ram_gb" -ge 8 ]; then OLLAMA_DEFAULT_MODEL="qwen3.5:9b"
    elif [ "$ram_gb" -ge 6 ]; then OLLAMA_DEFAULT_MODEL="qwen3.5:4b"
    elif [ "$ram_gb" -ge 4 ]; then OLLAMA_DEFAULT_MODEL="qwen3.5:2b"
    else OLLAMA_DEFAULT_MODEL="qwen3.5:0.8b"; fi
    echo "  Detected ${ram_gb} GB RAM -> using ${OLLAMA_DEFAULT_MODEL}"
fi
export OLLAMA_DEFAULT_MODEL

echo "[Step 3/5] Checking AI model (${OLLAMA_DEFAULT_MODEL})..."

if ollama list 2>/dev/null | grep -q "${OLLAMA_DEFAULT_MODEL}"; then
    echo "  Model ${OLLAMA_DEFAULT_MODEL} is ready!"
else
    echo "  Downloading ${OLLAMA_DEFAULT_MODEL} model (first time only)..."
    echo "  This may take several minutes depending on your internet speed."
    echo ""
    ollama pull "${OLLAMA_DEFAULT_MODEL}"
    if [ $? -ne 0 ]; then
        echo ""
        echo "  ERROR: Failed to download model."
        echo "  Please check your internet connection and try again."
        echo "  Or manually run: ollama pull ${OLLAMA_DEFAULT_MODEL}"
        echo ""
        read -p "  Press Enter to exit..."
        exit 1
    fi
    echo "  Model downloaded successfully!"
fi

# ========================================
# STEP 4: Install Python Dependencies
# ========================================
echo "[Step 4/5] Checking Python dependencies..."

INSTALL_OK=true
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "  Installing dependencies (first time only)..."
    if python3 -m pip install --only-binary :all: -r app/requirements.txt; then
        echo "  Dependencies installed successfully!"
    else
        echo "  Retrying with source builds allowed..."
        if python3 -m pip install -r app/requirements.txt; then
            echo "  Dependencies installed successfully!"
        else
            INSTALL_OK=false
        fi
    fi
else
    echo "  All dependencies installed!"
fi

if [ "$INSTALL_OK" = false ]; then
    echo ""
    echo "  ERROR: Failed to install dependencies."
    echo "  Please check the error above and try again."
    read -p "  Press Enter to exit..."
    exit 1
fi

# ========================================
# STEP 5: Launch snflwr.ai
# ========================================
echo ""
echo "========================================"
echo "  All systems ready!"
echo "========================================"
echo ""
echo "  App:       http://localhost:8000"
echo "  Dashboard: http://localhost:8000/dashboard"
echo "  AI Model:  ${OLLAMA_DEFAULT_MODEL}"
echo ""
echo "  Press Ctrl+C to stop the server"
echo "========================================"
echo ""

python3 -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000

echo ""
echo "========================================"
echo "Server stopped or encountered an error."
echo "========================================"
read -p "Press Enter to exit..."
""")

        # Make Unix launcher executable
        unix_launcher.chmod(0o755)

        print_success("Created run_snflwr.sh (macOS/Linux)")

    def create_documentation(self):
        """Create USB deployment documentation"""
        print_step("Creating documentation...")

        readme_content = """# snflwr.ai - USB Deployment

## [START] Quick Start

### Windows:
1. Double-click `run_snflwr.bat`
2. Wait for the server to start
3. Open browser to http://localhost:8000

### macOS/Linux:
1. Open Terminal in this folder
2. Run: `./run_snflwr.sh`
3. Open browser to http://localhost:8000

## [DIR] What's Included

- **app/** - snflwr.ai application code
- **data/** - Your SQLite database (KEEP THIS SAFE!)
- **logs/** - Application logs
- **backups/** - Database backup location
- **docs/** - Additional documentation

## [LOCKED] Privacy & Security

### Your Data Stays Local
- All data stored on THIS USB drive
- NO cloud uploads or external connections
- Database encrypted at rest
- Pull the USB = instant data portability

### First-Time Setup
1. Run the launcher script
2. Access parent dashboard: http://localhost:8000/dashboard
3. Dashboard password is in `DASHBOARD_PASSWORD.txt`
4. Create your first child profile

## [TIP] Usage Tips

### Moving to Another Computer
1. Stop the server (Ctrl+C)
2. Safely eject USB drive
3. Plug into new computer
4. Run launcher script
5. Everything works exactly the same!

### Backing Up Your Data
The database is in `data/snflwr.db`. To back up:
```bash
# Automatic backup
python app/scripts/backup_database.py

# Manual backup
cp data/snflwr.db backups/snflwr_backup_$(date +%Y%m%d).db
```

### Requirements
- Python 3.8 or higher
- 100MB free space on USB
- Internet connection (for AI model access)

## [HELP] Troubleshooting

### "Python not found"
Install Python 3.8+ from https://www.python.org/downloads/
Make sure to check "Add Python to PATH" during installation

### "Port 8000 already in use"
Another application is using port 8000. Edit the launcher script
and change `--port 8000` to `--port 8001`

### Database errors
Your database may be corrupted. Restore from backup:
```bash
cp backups/snflwr_backup_YYYYMMDD.db data/snflwr.db
```

### Permission errors (macOS/Linux)
Make launcher executable:
```bash
chmod +x run_snflwr.sh
```

## [DOCS] Additional Resources

- Full documentation: https://github.com/yourusername/snflwr-ai
- Report issues: https://github.com/yourusername/snflwr-ai/issues
- Database guide: See `docs/DATABASE_GUIDE.md` in main repository

## [SECURE] Security Reminders

[WARN] **IMPORTANT:**
- This USB contains COPPA-protected child data
- Keep the USB in a secure location
- Never share your dashboard password
- Regularly backup `data/snflwr.db`
- The dashboard password is in `DASHBOARD_PASSWORD.txt`

## [STATS] What Data is Stored?

- Child profiles (names, ages, grades)
- Learning conversations and questions
- Safety incident logs
- Parent account information
- Usage statistics

**All encrypted and stored ONLY on this USB drive.**

---

*snflwr.ai - Privacy-First K-12 AI Education*
"""

        readme_path = self.usb_root / 'README.md'
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)

        print_success("Created README.md")

        # Copy additional docs
        docs_to_copy = [
            'QUICKSTART.md',
            'docs/DATABASE_GUIDE.md',
        ]

        for doc_path in docs_to_copy:
            src = self.project_root / doc_path
            if src.exists():
                dst = self.usb_root / 'docs' / src.name
                shutil.copy2(src, dst)
                print_success(f"Copied {src.name}")

    def create_zip_package(self):
        """Package USB image as ZIP for distribution"""
        print_step("Creating ZIP package...")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        zip_name = f"SnflwrAI_USB_{timestamp}.zip"
        zip_path = self.output_dir / zip_name

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.usb_root):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(self.usb_root.parent)
                    zipf.write(file_path, arcname)

        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print_success(f"Created {zip_name} ({size_mb:.1f} MB)")

        return zip_path


def main():
    """Main entry point"""
    # Get output directory from args or use default
    if len(sys.argv) > 1:
        output_dir = Path(sys.argv[1])
    else:
        output_dir = Path.cwd() / 'dist'

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build USB image
    builder = USBImageBuilder(output_dir)

    try:
        builder.build()
        return 0
    except Exception as e:
        print_error(f"Build failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
