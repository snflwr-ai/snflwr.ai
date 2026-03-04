"""
Tests for core/thin_client.py — ThinClientManager

Coverage:
    - Manifest fetch (success, server failure with cache fallback, no cache)
    - Config application (env vars + singleton update, local overrides preserved)
    - Update detection (version mismatch, same version, force flag)
    - Download (streaming + checksum verification, checksum mismatch, missing URL)
    - Client registration (success, network failure)
    - Cache save/load round-trip
"""

import importlib.util
import json
import os
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import pytest


# ── Load module bypassing core/__init__.py ───────────────────────
# core/__init__.py imports AuthenticationManager which pulls in the full
# storage/cryptography chain. core/thin_client.py uses only stdlib, so
# we load it directly from the file and slot a fake config into sys.modules
# before execution.

@dataclass
class _FakeConfig:
    """Lightweight stand-in for system_config."""
    VERSION: str = "dev"
    OLLAMA_HOST: str = "http://localhost:11434"
    API_PORT: int = 39150
    OPEN_WEBUI_URL: str = "http://localhost:3000"
    BASE_URL: str = "http://localhost:39150"


# Pre-seed a fake config module so `from config import system_config` works
# inside thin_client without triggering the real heavy config → storage chain.
_fake_config_mod = MagicMock()
_fake_config_mod.system_config = _FakeConfig()
if "config" not in sys.modules:
    sys.modules["config"] = _fake_config_mod

_tc_path = Path(__file__).resolve().parent.parent / "core" / "thin_client.py"
_spec = importlib.util.spec_from_file_location("_thin_client_isolated", str(_tc_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

ThinClientManager = _mod.ThinClientManager
MANIFEST_CACHE_FILENAME = _mod.MANIFEST_CACHE_FILENAME


# ── Fixtures ─────────────────────────────────────────────────────

SAMPLE_MANIFEST = {
    "version": "1.2.0",
    "config": {
        "OLLAMA_BASE_URL": "http://server:11434",
        "API_PORT": "39150",
        "OPEN_WEBUI_URL": "http://server:3000",
        "BASE_URL": "http://server:39150",
    },
    "launcher_version": "1.2.0",
    "launcher_checksum": "",
    "launcher_url": "",
    "message": "Welcome",
    "force_update": False,
}


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "snflwr_data"
    d.mkdir()
    return d


@pytest.fixture
def manager(data_dir):
    return ThinClientManager("http://mgmt-server:39150", data_dir)


@pytest.fixture
def fake_config():
    """Return a fresh fake config and install it in sys.modules for the test.

    When the full test suite runs, earlier tests may import the real config
    module, so ``from config import system_config`` inside
    ThinClientManager methods would see the real singleton.  This fixture
    always injects a lightweight fake so tests remain isolated.
    """
    fc = _FakeConfig()
    mod = MagicMock()
    mod.system_config = fc
    with patch.dict(sys.modules, {"config": mod}):
        yield fc


def _patch_urlopen(return_value=None, side_effect=None):
    """Patch urlopen on the isolated module."""
    return patch.object(_mod, "urlopen", return_value=return_value, side_effect=side_effect)


# ── Manifest fetch ───────────────────────────────────────────────


class TestFetchManifest:
    def test_fetch_success(self, manager):
        body = json.dumps(SAMPLE_MANIFEST).encode()
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = body

        with _patch_urlopen(return_value=mock_resp):
            manifest = manager.fetch_manifest(timeout=5)

        assert manifest is not None
        assert manifest["version"] == "1.2.0"
        assert manager.manifest_path.exists()

    def test_fetch_failure_uses_cache(self, manager):
        manager._save_manifest(SAMPLE_MANIFEST)

        with _patch_urlopen(side_effect=URLError("offline")):
            manifest = manager.fetch_manifest()

        assert manifest is not None
        assert manifest["version"] == "1.2.0"

    def test_fetch_failure_no_cache_returns_none(self, manager):
        with _patch_urlopen(side_effect=URLError("offline")):
            manifest = manager.fetch_manifest()

        assert manifest is None


# ── Config application ───────────────────────────────────────────


class TestApplyConfig:
    def test_sets_env_vars(self, manager, fake_config):
        for key in SAMPLE_MANIFEST["config"]:
            os.environ.pop(key, None)

        manager.apply_config(SAMPLE_MANIFEST)

        assert os.environ.get("OLLAMA_BASE_URL") == "http://server:11434"
        assert os.environ.get("BASE_URL") == "http://server:39150"

        for key in SAMPLE_MANIFEST["config"]:
            os.environ.pop(key, None)

    def test_preserves_local_overrides(self, manager, fake_config):
        os.environ["OLLAMA_BASE_URL"] = "http://local-override:11434"

        try:
            manager.apply_config(SAMPLE_MANIFEST)
            assert os.environ["OLLAMA_BASE_URL"] == "http://local-override:11434"
        finally:
            os.environ.pop("OLLAMA_BASE_URL", None)

    def test_updates_singleton(self, manager, fake_config):
        os.environ.pop("OLLAMA_BASE_URL", None)

        try:
            manager.apply_config(SAMPLE_MANIFEST)
            assert fake_config.OLLAMA_HOST == "http://server:11434"
        finally:
            for key in SAMPLE_MANIFEST["config"]:
                os.environ.pop(key, None)

    def test_int_coercion(self, manager, fake_config):
        """API_PORT should be coerced to int on the singleton."""

        os.environ.pop("API_PORT", None)
        try:
            manager.apply_config(SAMPLE_MANIFEST)
            assert fake_config.API_PORT == 39150
            assert isinstance(fake_config.API_PORT, int)
        finally:
            os.environ.pop("API_PORT", None)

    def test_empty_config_is_no_op(self, manager, fake_config):

        manager.apply_config({"config": {}})
        manager.apply_config({})


# ── Update detection ─────────────────────────────────────────────


class TestCheckUpdate:
    def test_same_version_no_update(self, manager, fake_config):

        manifest = {**SAMPLE_MANIFEST, "launcher_version": fake_config.VERSION}
        assert manager.check_update_available(manifest) is False

    def test_different_version_has_update(self, manager, fake_config):

        manifest = {**SAMPLE_MANIFEST, "launcher_version": "99.99.99"}
        assert manager.check_update_available(manifest) is True

    def test_empty_version_no_update(self, manager, fake_config):

        manifest = {**SAMPLE_MANIFEST, "launcher_version": ""}
        assert manager.check_update_available(manifest) is False


# ── Download ─────────────────────────────────────────────────────


class TestDownloadUpdate:
    def test_no_url_returns_none(self, manager, data_dir):
        manifest = {**SAMPLE_MANIFEST, "launcher_url": ""}
        assert manager.download_update(manifest, data_dir) is None

    def test_successful_download(self, manager, data_dir):
        content = b"fake-zip-content-for-test"
        sha = hashlib.sha256(content).hexdigest()
        manifest = {
            **SAMPLE_MANIFEST,
            "launcher_url": "http://mgmt-server/update.zip",
            "launcher_checksum": f"sha256:{sha}",
        }

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [content, b""]

        with _patch_urlopen(return_value=mock_resp):
            result = manager.download_update(manifest, data_dir)

        assert result is not None
        assert result.name == "launcher_update.zip"
        assert result.read_bytes() == content

    def test_checksum_mismatch_returns_none(self, manager, data_dir):
        manifest = {
            **SAMPLE_MANIFEST,
            "launcher_url": "http://mgmt-server/update.zip",
            "launcher_checksum": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        }

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"corrupted-data", b""]

        with _patch_urlopen(return_value=mock_resp):
            result = manager.download_update(manifest, data_dir)

        assert result is None
        assert not (data_dir / "launcher_update.zip.tmp").exists()

    def test_network_error_returns_none(self, manager, data_dir):
        manifest = {
            **SAMPLE_MANIFEST,
            "launcher_url": "http://mgmt-server/update.zip",
        }

        with _patch_urlopen(side_effect=URLError("timeout")):
            result = manager.download_update(manifest, data_dir)

        assert result is None


# ── Client registration ──────────────────────────────────────────


class TestRegisterClient:
    def test_register_success(self, manager, fake_config):

        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200

        with _patch_urlopen(return_value=mock_resp):
            assert manager.register_client() is True

    def test_register_failure(self, manager, fake_config):

        with _patch_urlopen(side_effect=URLError("offline")):
            assert manager.register_client() is False


# ── Cache round-trip ─────────────────────────────────────────────


class TestManifestCache:
    def test_save_and_load(self, manager):
        manager._save_manifest(SAMPLE_MANIFEST)
        loaded = manager._load_cached_manifest()
        assert loaded == SAMPLE_MANIFEST

    def test_load_missing_returns_none(self, manager):
        assert manager._load_cached_manifest() is None

    def test_load_corrupt_returns_none(self, manager):
        manager.manifest_path.write_text("not-valid-json{{{")
        assert manager._load_cached_manifest() is None
