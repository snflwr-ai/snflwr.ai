#!/bin/bash

# snflwr.ai Bootstrap Setup
# Ensures Python 3 is installed, then runs the interactive installer.
# Usage: curl the repo, then run ./setup.sh

set -e

echo "=========================================="
echo "  snflwr.ai - Bootstrap Setup"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=8

# ---------------------------------------------------------------------------
# Detect a usable Python 3 interpreter
# ---------------------------------------------------------------------------
find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" &>/dev/null; then
            # Verify it is actually Python 3.8+
            if "$candidate" -c "import sys; exit(0 if sys.version_info >= ($REQUIRED_PYTHON_MAJOR, $REQUIRED_PYTHON_MINOR) else 1)" 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# Install Python 3 via the system package manager
# ---------------------------------------------------------------------------
install_python() {
    echo -e "${YELLOW}Python $REQUIRED_PYTHON_MAJOR.$REQUIRED_PYTHON_MINOR+ is required but not found.${NC}"
    echo ""

    OS="$(uname -s)"
    case "$OS" in
        Linux)
            install_python_linux
            ;;
        Darwin)
            install_python_macos
            ;;
        *)
            echo -e "${RED}Unsupported OS: $OS${NC}"
            echo "Please install Python $REQUIRED_PYTHON_MAJOR.$REQUIRED_PYTHON_MINOR+ manually from https://www.python.org/downloads/"
            exit 1
            ;;
    esac
}

install_python_linux() {
    if command -v apt-get &>/dev/null; then
        echo -e "${YELLOW}Installing Python via apt...${NC}"
        sudo apt-get update -qq
        sudo apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf &>/dev/null; then
        echo -e "${YELLOW}Installing Python via dnf...${NC}"
        sudo dnf install -y python3 python3-pip
    elif command -v yum &>/dev/null; then
        echo -e "${YELLOW}Installing Python via yum...${NC}"
        sudo yum install -y python3 python3-pip
    elif command -v pacman &>/dev/null; then
        echo -e "${YELLOW}Installing Python via pacman...${NC}"
        sudo pacman -S --noconfirm python python-pip
    elif command -v zypper &>/dev/null; then
        echo -e "${YELLOW}Installing Python via zypper...${NC}"
        sudo zypper install -y python3 python3-pip
    elif command -v apk &>/dev/null; then
        echo -e "${YELLOW}Installing Python via apk...${NC}"
        sudo apk add python3 py3-pip
    else
        echo -e "${RED}Could not detect a supported package manager.${NC}"
        echo "Please install Python $REQUIRED_PYTHON_MAJOR.$REQUIRED_PYTHON_MINOR+ manually:"
        echo "  https://www.python.org/downloads/"
        exit 1
    fi
}

install_python_macos() {
    if command -v brew &>/dev/null; then
        echo -e "${YELLOW}Installing Python via Homebrew...${NC}"
        brew install python@3
    else
        echo -e "${YELLOW}Homebrew not found. Installing Homebrew first...${NC}"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add brew to PATH for the current session
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi

        echo -e "${YELLOW}Installing Python via Homebrew...${NC}"
        brew install python@3
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PYTHON_CMD=$(find_python) || true

if [ -z "$PYTHON_CMD" ]; then
    install_python

    # Re-check after install
    PYTHON_CMD=$(find_python) || true
    if [ -z "$PYTHON_CMD" ]; then
        echo -e "${RED}Python installation succeeded but python3 is still not on PATH.${NC}"
        echo "Try opening a new terminal and running this script again."
        exit 1
    fi
fi

PYTHON_VERSION=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
echo -e "${GREEN}Using Python $PYTHON_VERSION ($PYTHON_CMD)${NC}"

# Ensure pip is available
if ! "$PYTHON_CMD" -m pip --version &>/dev/null; then
    echo -e "${YELLOW}Installing pip...${NC}"
    if ! "$PYTHON_CMD" -m ensurepip --upgrade 2>/dev/null; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get install -y python3-pip
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y python3-pip
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm python-pip
        elif command -v apk &>/dev/null; then
            sudo apk add py3-pip
        else
            echo -e "${RED}Could not install pip. Please install it manually.${NC}"
            exit 1
        fi
    fi
fi

# Ensure venv module is available (some distros strip it out)
if ! "$PYTHON_CMD" -c "import venv" &>/dev/null; then
    echo -e "${YELLOW}Installing python3-venv...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y python3-venv
    fi
fi

# Ensure tkinter is available (required for the GUI launcher)
if ! "$PYTHON_CMD" -c "import tkinter" &>/dev/null; then
    echo -e "${YELLOW}Installing python3-tk (required for GUI launcher)...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y python3-tk
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3-tkinter
    elif command -v yum &>/dev/null; then
        sudo yum install -y python3-tkinter
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm tk
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y python3-tk
    elif command -v apk &>/dev/null; then
        sudo apk add py3-tkinter
    else
        echo -e "${YELLOW}Could not install tkinter automatically. GUI launcher will fall back to terminal mode.${NC}"
        echo "  Install manually: sudo apt install python3-tk"
    fi
fi

echo ""
echo -e "${GREEN}Python is ready. Launching snflwr.ai installer...${NC}"
echo ""

# Change to the script's directory (handles being called from elsewhere)
cd "$(dirname "$0")"

exec "$PYTHON_CMD" install.py "$@"
