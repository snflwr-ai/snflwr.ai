"""Ollama install/start/model-pull flow and the snflwr.ai model wrapper."""

import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .detection import detect_existing_model
from .platform_utils import _refresh_windows_path
from .ui import (
    ask_question,
    ask_yes_no,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)


def check_ollama_installed():
    """Check if Ollama is installed"""
    return shutil.which("ollama") is not None


def install_ollama():
    """Install Ollama based on the current platform"""
    system = platform.system()

    if system == "Linux":
        print_info("Installing Ollama via official install script...")
        try:
            subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                check=True,
            )
            print_success("Ollama installed successfully")
            return True
        except subprocess.CalledProcessError:
            print_error("Automatic Ollama installation failed")
            print_info("Please install manually: https://ollama.com/download/linux")
            return False

    elif system == "Darwin":
        # Check if Homebrew is available
        if shutil.which("brew"):
            print_info("Installing Ollama via Homebrew...")
            try:
                subprocess.run(["brew", "install", "ollama"], check=True)
                print_success("Ollama installed successfully")
                return True
            except subprocess.CalledProcessError:
                pass

        print_info("Installing Ollama via official install script...")
        try:
            subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                check=True,
            )
            print_success("Ollama installed successfully")
            return True
        except subprocess.CalledProcessError:
            print_error("Automatic Ollama installation failed")
            print_info("Please install manually: https://ollama.com/download/mac")
            return False

    elif system == "Windows":
        # Try winget first (available on Windows 10 1709+ and Windows 11)
        if shutil.which("winget"):
            print_info("Installing Ollama via winget...")
            try:
                subprocess.run(
                    [
                        "winget",
                        "install",
                        "Ollama.Ollama",
                        "-e",
                        "--accept-source-agreements",
                    ],
                    check=True,
                )
                # Refresh PATH so the current process can find the new binary
                _refresh_windows_path()
                print_success("Ollama installed successfully")
                return True
            except subprocess.CalledProcessError:
                pass

        print_error("Automatic Ollama installation failed")
        print_info(
            "Please download and install from: https://ollama.com/download/windows"
        )
        print_info("After installing, re-run this installer")
        return False

    else:
        print_error(f"Unsupported platform: {system}")
        print_info("Please install Ollama manually: https://ollama.com/download")
        return False


def ensure_ollama_running():
    """Ensure the Ollama service is running, start it if needed"""
    # Check if already running
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        print_success("Ollama service is running")
        return True
    except (urllib.error.URLError, OSError):
        pass

    print_info("Starting Ollama service...")

    system = platform.system()

    def _api_reachable():
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
            return True
        except (urllib.error.URLError, OSError):
            return False

    if system == "Linux":
        # Try systemctl first
        try:
            subprocess.run(["systemctl", "start", "ollama"], check=False)
            time.sleep(3)
            if _api_reachable():
                print_success("Ollama service started via systemd")
                return True
        except FileNotFoundError:
            pass

    elif system == "Windows":
        # On Windows, Ollama runs as a background app from the user's Start Menu.
        # Try launching 'ollama app' via the installed shortcut.
        app_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
        app_exe = app_dir / "ollama app.exe"
        if app_exe.exists():
            try:
                kwargs = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                subprocess.Popen([str(app_exe)], **kwargs)
                time.sleep(3)
                if _api_reachable():
                    print_success("Ollama app started")
                    return True
            except OSError:
                pass

    # Fall back to starting ollama serve in the background
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if system == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        print_error("Could not start Ollama - binary not found")
        return False

    # Wait for it to come up
    for i in range(15):
        time.sleep(2)
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
            print_success("Ollama service started")
            return True
        except (urllib.error.URLError, OSError):
            pass

    print_error("Ollama service did not start within 30 seconds")
    print_info("Try starting manually: ollama serve")
    return False


def pull_default_model(model="gemma4:e4b"):
    """Pull the default AI model via Ollama"""
    print_info(f"Checking for model '{model}'...")

    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, check=True
        )
        # Match the exact model name at the start of a line to avoid
        # false positives like "qwen3.5:9b-instruct" matching "qwen3.5:9b"
        for line in result.stdout.splitlines():
            # ollama list output: "NAME  ID  SIZE  MODIFIED"
            line_model = line.split()[0] if line.strip() else ""
            if line_model == model:
                print_success(f"Model '{model}' is already available")
                return True
    except (subprocess.CalledProcessError, IndexError):
        pass

    print_info(f"Pulling model '{model}' (this may take several minutes)...")
    try:
        subprocess.run(["ollama", "pull", model], check=True)
        print_success(f"Model '{model}' downloaded successfully")
        return True
    except subprocess.CalledProcessError:
        print_error(f"Failed to pull model '{model}'")
        print_info(f"You can retry later with: ollama pull {model}")
        return False


def build_snflwr_wrapper(base_model: str) -> bool:
    """Build the user-facing 'snflwr.ai' model on top of a qwen3.5 base.

    Reads models/Snflwr_AI_Kids.modelfile, substitutes the FROM line with
    the chosen base, and runs `ollama create snflwr.ai -f <tmpfile>`.

    The user-facing chat model is always 'snflwr.ai' — kids never see the
    raw qwen3.5 tag in the Open WebUI dropdown. The wrapper bundles the
    K-12 STEM tutor system prompt + sampling parameters (incl. repeat_penalty
    to prevent reasoning loops) + safety stop sequences from the modelfile.

    Returns True on success, False on any failure (caller should fall back
    to using the base model directly).
    """
    repo_root = Path(__file__).resolve().parent.parent
    modelfile_src = repo_root / "models" / "Snflwr_AI_Kids.modelfile"
    if not modelfile_src.is_file():
        print_warning(f"Modelfile not found at {modelfile_src}")
        return False

    print_info(f"Building 'snflwr.ai' on top of '{base_model}'...")

    # Substitute FROM line
    try:
        original = modelfile_src.read_text()
    except OSError as exc:
        print_warning(f"Could not read modelfile: {exc}")
        return False

    rewritten_lines = []
    for line in original.splitlines():
        if line.startswith("FROM "):
            rewritten_lines.append(f"FROM {base_model}")
        else:
            rewritten_lines.append(line)
    rewritten = "\n".join(rewritten_lines) + "\n"

    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".modelfile", delete=False
    ) as tmp:
        tmp.write(rewritten)
        tmp_path = tmp.name

    try:
        subprocess.run(
            ["ollama", "create", "snflwr.ai", "-f", tmp_path],
            check=True,
            capture_output=True,
            text=True,
        )
        print_success("'snflwr.ai' built successfully")
        return True
    except subprocess.CalledProcessError as exc:
        print_warning("Failed to build 'snflwr.ai' wrapper")
        if exc.stderr:
            print_info(exc.stderr.strip())
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def choose_model(total_ram_gb: Optional[float] = None) -> str:
    """Choose a base model to build the snflwr.ai wrapper on, based on RAM.

    Returns a base tag to pull. The default backbone is gemma4:e4b (won the
    2026-06-17 tutoring-quality backbone bake-off; see
    evals/tutoring/backbone_bakeoff.py); boxes too small for it fall back to
    the small qwen3.5 tiers. The user-facing chat model is always 'snflwr.ai',
    built as a wrapper around this base.
    """
    # Model options: tag, param count, approximate download size, minimum RAM.
    # Ascending min_ram — the loop keeps the largest the box can run, so
    # gemma4:e4b is the pick on any box with >= 16 GB.
    models = [
        ("qwen3.5:0.8b", "0.8B", "~0.5 GB download", 2),
        ("qwen3.5:2b", "2B", "~1.3 GB download", 6),
        ("qwen3.5:4b", "4B", "~2.5 GB download", 8),
        ("gemma4:e4b", "E4B (MoE)", "~10 GB download", 16),
    ]

    # Pick recommended model based on detected RAM
    if total_ram_gb is not None:
        recommended = "qwen3.5:0.8b"  # fallback
        for tag, _, _, min_ram in models:
            if total_ram_gb >= min_ram:
                recommended = tag
    else:
        recommended = (
            "gemma4:e4b"  # assumes a real (>=16 GB) box when RAM detection fails
        )

    # Allow env var override. Accept either BASE_MODEL or a legacy
    # OLLAMA_DEFAULT_MODEL pointing at a qwen3.5 tag (we ignore the new
    # 'snflwr.ai' value here — that's the wrapper, not a base).
    env_base = os.getenv("BASE_MODEL")
    if env_base:
        print_info(f"Using base model from BASE_MODEL: {env_base}")
        return env_base
    env_model = os.getenv("OLLAMA_DEFAULT_MODEL")
    if env_model and env_model != "snflwr.ai":
        print_info(f"Using base model from OLLAMA_DEFAULT_MODEL: {env_model}")
        return env_model

    print_info(
        "Choose a base model (gemma4:e4b is the recommended backbone;\n"
        "snflwr.ai is built as a wrapper on top of your choice):\n"
    )

    for i, (tag, params, size, min_ram) in enumerate(models, 1):
        rec = " ← recommended" if tag == recommended else ""
        ram_note = f"needs ~{min_ram} GB RAM"
        print(f"  {i}. {tag:<14} {params:>4} params   {size:<18} ({ram_note}){rec}")

    print(f"\n  s. Skip model download for now")

    if total_ram_gb is not None:
        print(f"\n  Your system: {total_ram_gb:.0f} GB RAM")

    # Find the index of the recommended model (1-based)
    rec_idx = next(
        (i for i, (tag, *_) in enumerate(models, 1) if tag == recommended),
        4,  # fallback index if recommended not found
    )

    while True:
        choice = ask_question(
            f"Select model (1-{len(models)}, or s to skip)", str(rec_idx)
        )
        if choice.lower() == "s":
            return ""  # empty string means skip
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                tag = models[idx][0]
                min_ram = models[idx][3]
                if total_ram_gb is not None and total_ram_gb < min_ram:
                    print_warning(
                        f"Your system has {total_ram_gb:.0f} GB RAM but {tag} needs ~{min_ram} GB."
                    )
                    if not ask_yes_no("Continue anyway?", default=False):
                        continue
                return tag
        except ValueError:
            pass
        print_error(f"Please enter a number 1-{len(models)} or 's' to skip")


def setup_ollama(total_ram_gb: Optional[float] = None) -> str:
    """Full Ollama setup: install, start, pull base, and build snflwr.ai.

    Returns the model tag to write into OLLAMA_DEFAULT_MODEL — typically
    'snflwr.ai' (the wrapper), falling back to the raw base tag if the
    wrapper build fails. Returns '' on failure/skip so callers know to
    leave the .env unset.
    """
    print_header("Ollama Setup")

    print(
        """
snflwr.ai uses Ollama to run AI models locally.
This ensures all data stays on your device - nothing is sent to the cloud.
    """
    )

    # Step 1: Check/install Ollama
    if check_ollama_installed():
        print_success("Ollama is installed")
    else:
        print_warning("Ollama is not installed")
        if ask_yes_no("Install Ollama now?", default=True):
            if not install_ollama():
                print_warning("Skipping Ollama setup - you can install it later")
                return ""
        else:
            print_warning("Skipping Ollama setup")
            print_info("Install Ollama later from: https://ollama.com/download")
            return ""

    # Step 2: Ensure Ollama is running
    if not ensure_ollama_running():
        print_warning("Could not start Ollama service")
        print_info("Start it manually with: ollama serve")
        return ""

    # Step 3: Check for existing models before prompting
    existing_model = detect_existing_model()
    if existing_model:
        print_success(f"Found existing model: {existing_model}")
        if ask_yes_no(f"Use '{existing_model}' as the default model?", default=True):
            print_success("Ollama setup complete")
            return existing_model

    # Step 4: Choose and pull a base model
    base_model = choose_model(total_ram_gb)
    if not base_model:
        if existing_model:
            print_info(f"Keeping existing model: {existing_model}")
            return existing_model
        print_info("Skipping model download. You can pull one later:")
        print_info("  ollama pull qwen3.5:9b")
        print_success("Ollama setup complete (no model pulled)")
        return ""

    if not pull_default_model(base_model):
        print_warning(
            f"Base model pull failed - you can retry with: ollama pull {base_model}"
        )
        return base_model  # fall back to base so .env records something usable

    # Step 5: Build the snflwr.ai wrapper. This is what kids see in the
    # Open WebUI dropdown — never the raw qwen3.5 tag.
    if build_snflwr_wrapper(base_model):
        print_success("Ollama setup complete")
        return "snflwr.ai"

    # Wrapper build failed — fall back to the raw base so chat still works
    print_warning(
        "snflwr.ai wrapper build failed — falling back to base model. "
        f"Kids will see '{base_model}' in the dropdown until rebuilt."
    )
    return base_model


def setup_safety_model(ollama_available: bool = True) -> bool:
    """Ask if children will use the system and optionally pull the safety model.

    The safety model (llama-guard3:1b) powers the ML-based semantic classifier
    in the content-safety pipeline.  Without it the deterministic pattern-
    matching stages still protect, but the LLM layer adds significantly deeper
    coverage.

    Returns True if the safety model should be enabled.
    """
    print_header("Child Safety Configuration")

    print(
        """
snflwr.ai includes a multi-layer content safety pipeline that filters
every message for age-inappropriate content, PII, and harmful material.

If children will be using this system, an additional AI safety model
(llama-guard3:1b, ~1 GB download) can be installed. This model adds a
semantic classification layer on top of the existing pattern-matching
filters for significantly stronger protection.
    """
    )

    enable = ask_yes_no("Will children be using this system?", default=True)

    if not enable:
        print_info("Safety model will not be downloaded.")
        print_info("Deterministic content filters are still active.")
        return False

    if not ollama_available:
        print_warning(
            "Ollama is not available - safety model cannot be downloaded now."
        )
        print_info("After installing Ollama, run:  ollama pull llama-guard3:1b")
        return True  # Mark as enabled so .env records it for startup scripts

    print_info("Downloading safety model (llama-guard3:1b)...")
    if pull_default_model("llama-guard3:1b"):
        print_success("Safety model installed - semantic content classifier is active")
    else:
        print_warning("Safety model download failed.")
        print_info("You can retry later with:  ollama pull llama-guard3:1b")

    return True
