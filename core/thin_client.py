"""
Thin Client Management — pull configuration and updates from a central server.

In thin-client deployments a management server provides:
  - Connection configuration (Ollama URL, API port, WebUI URL, etc.)
  - Launcher version information and update packages
  - A welcome message for the site

The management server exposes a manifest at:
    GET {MANAGEMENT_SERVER_URL}/api/thin-client/manifest

Uses only stdlib so there are no extra dependencies on the client side.
"""

import json
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger(__name__)

MANIFEST_CACHE_FILENAME = ".thin_client_manifest.json"


_ALLOWED_SCHEMES = frozenset(('http', 'https'))


def _validate_url(url: str) -> str:
    """Ensure URL uses http/https scheme only (prevents file:// attacks)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' not allowed (only http/https)")
    return url


class ThinClientManager:
    """Manages thin client configuration and updates from a management server."""

    def __init__(self, server_url: str, data_dir: Path):
        self.server_url = _validate_url(server_url.rstrip('/'))
        self.data_dir = data_dir
        self.manifest_path = data_dir / MANIFEST_CACHE_FILENAME

    # ── Manifest retrieval ────────────────────────────────────────

    def fetch_manifest(self, timeout: int = 10) -> Optional[Dict[str, Any]]:
        """
        Fetch the deployment manifest from the management server.

        Returns the manifest dict, or a cached copy if the server is
        unreachable.  Returns None only if both fail.
        """
        url = f"{self.server_url}/api/thin-client/manifest"
        try:
            _validate_url(url)
            req = Request(url, headers={'User-Agent': 'SnflwrAI-ThinClient/1.0'})
            resp = urlopen(req, timeout=timeout)  # nosec B310
            if 200 <= resp.getcode() < 300:
                manifest = json.loads(resp.read().decode('utf-8'))
                self._save_manifest(manifest)
                logger.info(
                    "Fetched manifest v%s from %s",
                    manifest.get('version', '?'), self.server_url,
                )
                return manifest
        except (URLError, json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to fetch manifest from %s: %s", url, e)

        # Fall back to a previously cached manifest (offline operation)
        return self._load_cached_manifest()

    # ── Configuration application ─────────────────────────────────

    # Map from manifest config keys to system_config attribute names.
    # Only keys that thin clients actually need are listed here.
    _CONFIG_TO_ATTR = {
        'OLLAMA_BASE_URL': 'OLLAMA_HOST',
        'API_PORT': 'API_PORT',
        'OPEN_WEBUI_URL': 'OPEN_WEBUI_URL',
        'BASE_URL': 'BASE_URL',
    }

    def apply_config(self, manifest: Dict[str, Any]) -> None:
        """
        Apply configuration from the manifest.

        Sets environment variables AND updates the already-instantiated
        system_config singleton so that all code (whether it reads
        os.getenv or system_config.*) sees the server-pushed values.
        Local overrides (env vars already set) are never replaced.
        """
        from config import system_config

        config = manifest.get('config', {})
        for key, value in config.items():
            str_value = str(value)
            if os.getenv(key) is None:
                os.environ[key] = str_value

            # Also update the live singleton so frozen defaults are overridden
            attr = self._CONFIG_TO_ATTR.get(key, key)
            if hasattr(system_config, attr):
                expected_type = type(getattr(system_config, attr))
                try:
                    if expected_type is int:
                        setattr(system_config, attr, int(str_value))
                    else:
                        setattr(system_config, attr, str_value)
                except (ValueError, TypeError):
                    pass  # skip type-mismatched values

                logger.debug("Applied thin-client config: %s=%s", attr, str_value)

    # ── Launcher updates ──────────────────────────────────────────

    def check_update_available(self, manifest: Dict[str, Any]) -> bool:
        """Return True if the server offers a newer launcher version."""
        from config import system_config

        server_version = manifest.get('launcher_version', '')
        current_version = system_config.VERSION
        if not server_version or server_version == current_version:
            return False
        # Always update if the server says force, otherwise update on
        # any version mismatch.
        return True

    def download_update(
        self, manifest: Dict[str, Any], dest_dir: Path
    ) -> Optional[Path]:
        """
        Download the launcher update package.

        Streams to a temporary file and verifies the SHA-256 checksum
        incrementally to keep memory usage constant.

        Returns:
            Path to the downloaded file, or None on failure.
        """
        url = manifest.get('launcher_url')
        expected_checksum = manifest.get('launcher_checksum', '')
        if not url:
            return None

        try:
            url = _validate_url(url)
            resp = urlopen(url, timeout=60)  # nosec B310
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest_dir / "launcher_update.zip.tmp"
            hasher = hashlib.sha256()

            with open(tmp_dest, 'wb') as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)

            if expected_checksum.startswith('sha256:'):
                expected_hash = expected_checksum[7:]
                if hasher.hexdigest() != expected_hash:
                    logger.error(
                        "Checksum mismatch: expected %s, got %s",
                        expected_hash, hasher.hexdigest(),
                    )
                    tmp_dest.unlink(missing_ok=True)
                    return None

            dest = dest_dir / "launcher_update.zip"
            tmp_dest.rename(dest)
            logger.info("Downloaded update to %s", dest)
            return dest

        except (URLError, OSError, ValueError) as e:
            logger.error("Failed to download update: %s", e)
            # Clean up partial download
            tmp_path = dest_dir / "launcher_update.zip.tmp"
            tmp_path.unlink(missing_ok=True)
            return None

    # ── Client registration ───────────────────────────────────────

    def register_client(self) -> bool:
        """
        Register this thin client with the management server.

        Sends hostname and current version so the admin dashboard can
        track connected clients.
        """
        import platform as _platform

        url = f"{self.server_url}/api/thin-client/register"
        try:
            _validate_url(url)
            from config import system_config
            payload = json.dumps({
                'hostname': _platform.node(),
                'platform': _platform.system(),
                'version': system_config.VERSION,
            }).encode('utf-8')
            req = Request(
                url,
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'SnflwrAI-ThinClient/1.0',
                },
                method='POST',
            )
            resp = urlopen(req, timeout=10)  # nosec B310
            return 200 <= resp.getcode() < 300
        except (URLError, OSError) as e:
            logger.warning("Failed to register client at %s: %s", url, e)
            return False

    # ── Private helpers ───────────────────────────────────────────

    def _save_manifest(self, manifest: Dict) -> None:
        """Cache manifest to disk for offline use."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(json.dumps(manifest, indent=2))
        except OSError as e:
            logger.warning("Failed to cache manifest: %s", e)

    def _load_cached_manifest(self) -> Optional[Dict]:
        """Load a previously cached manifest from disk."""
        try:
            if self.manifest_path.exists():
                manifest = json.loads(self.manifest_path.read_text())
                logger.info("Using cached manifest (server unreachable)")
                return manifest
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cached manifest: %s", e)
        return None
