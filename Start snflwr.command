#!/bin/bash
# snflwr.ai Launcher — friendly name for USB root (macOS)
# Delegates to the unified launcher, falls back through the chain

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Try the unified launcher first (prefer venv python for tkinter + deps)
if [ -x "venv/bin/python3" ] && [ -f "launcher/main.py" ]; then
    if venv/bin/python3 -c "import tkinter" 2>/dev/null; then
        venv/bin/python3 launcher/main.py &
        disown
        exit 0
    fi
fi

# Fallback: simple GUI launcher via venv
if [ -x "venv/bin/python3" ] && [ -f "launcher/app.py" ]; then
    if venv/bin/python3 -c "import tkinter" 2>/dev/null; then
        venv/bin/python3 launcher/app.py &
        disown
        exit 0
    fi
fi

# Fallback: system python
if [ -f "launcher/main.py" ] && command -v python3 &>/dev/null; then
    if python3 -c "import tkinter" 2>/dev/null; then
        python3 launcher/main.py &
        disown
        exit 0
    fi
fi

# Last resort: terminal startup
if [ -f "start_snflwr.sh" ]; then
    exec bash start_snflwr.sh
fi

echo "ERROR: snflwr.ai startup scripts not found."
echo "Run setup first:  python3 install.py"
read -p "Press Enter to close..."
