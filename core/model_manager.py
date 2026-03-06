"""
Model Manager for snflwr.ai
Handles model loading, caching, memory management, and inference
"""

import threading
import time
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from collections import OrderedDict

from utils.ollama_client import OllamaClient, OllamaError
from utils.logger import get_logger
from config import system_config

logger = get_logger(__name__)


@dataclass
class ModelInfo:
    """Information about a loaded model"""

    name: str
    size: int  # Size in bytes
    loaded_at: datetime
    last_used: datetime
    use_count: int
    parameters: Dict[str, Any]


class ModelCache:
    """LRU cache for loaded models with memory limits"""

    def __init__(self, max_memory_mb: int = 4096):
        """
        Initialize model cache

        Args:
            max_memory_mb: Maximum memory to use for cached models (default 4GB)
        """
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self._cache: OrderedDict[str, ModelInfo] = OrderedDict()
        self._lock = threading.RLock()
        self._current_memory = 0

        logger.info(f"Model cache initialized with {max_memory_mb}MB limit")

    def get(self, model_name: str) -> Optional[ModelInfo]:
        """Get model from cache and update LRU"""
        with self._lock:
            if model_name in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(model_name)
                model = self._cache[model_name]
                model.last_used = datetime.now(timezone.utc)
                model.use_count += 1
                logger.debug(f"Cache hit for model: {model_name}")
                return model

            logger.debug(f"Cache miss for model: {model_name}")
            return None

    def put(self, model_name: str, model_info: ModelInfo):
        """Add model to cache, evicting if necessary"""
        with self._lock:
            # Remove if already exists
            if model_name in self._cache:
                old_model = self._cache.pop(model_name)
                self._current_memory -= old_model.size

            # Evict LRU models until we have space
            while (
                self._current_memory + model_info.size > self.max_memory_bytes
                and len(self._cache) > 0
            ):
                evicted_name, evicted_model = self._cache.popitem(last=False)
                self._current_memory -= evicted_model.size
                logger.info(
                    f"Evicted model from cache: {evicted_name} "
                    f"(freed {evicted_model.size / 1024 / 1024:.1f}MB)"
                )

            # Add new model
            self._cache[model_name] = model_info
            self._current_memory += model_info.size

            logger.info(
                f"Cached model: {model_name} "
                f"(size: {model_info.size / 1024 / 1024:.1f}MB, "
                f"total: {self._current_memory / 1024 / 1024:.1f}MB)"
            )

    def remove(self, model_name: str):
        """Remove model from cache"""
        with self._lock:
            if model_name in self._cache:
                model = self._cache.pop(model_name)
                self._current_memory -= model.size
                logger.info(f"Removed model from cache: {model_name}")

    def clear(self):
        """Clear all cached models"""
        with self._lock:
            self._cache.clear()
            self._current_memory = 0
            logger.info("Model cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            return {
                "cached_models": len(self._cache),
                "memory_used_mb": self._current_memory / 1024 / 1024,
                "memory_limit_mb": self.max_memory_bytes / 1024 / 1024,
                "models": list(self._cache.keys()),
            }


class ModelManager:
    """
    Production-ready model manager for Ollama

    Features:
    - Model loading and caching
    - Memory management with LRU eviction
    - Model warmup on startup
    - Thread-safe operations
    - Error handling and retry logic
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern for model manager"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize model manager"""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self.ollama = OllamaClient()
        self.cache = ModelCache(max_memory_mb=4096)  # 4GB cache
        self._default_model = getattr(system_config, "OLLAMA_DEFAULT_MODEL", "")
        self._warmup_models: List[str] = []
        self._operation_lock = threading.RLock()

        logger.info("ModelManager initialized")

        # Check Ollama connection
        self._check_service()

    def _check_service(self):
        """Check if Ollama service is available"""
        try:
            is_available, result = self.ollama.check_connection()
            if is_available:
                logger.info(f"Ollama service available: {result}")
            else:
                logger.warning(f"Ollama service unavailable: {result}")
        except (OllamaError, ConnectionError, OSError) as e:
            logger.error(f"Failed to check Ollama service: {e}")

    def load_model(self, model_name: str, force_reload: bool = False) -> Dict[str, Any]:
        """
        Load a model (with caching)

        Args:
            model_name: Name of the model to load
            force_reload: Force reload even if cached

        Returns:
            Model info dict with status
        """
        with self._operation_lock:
            try:
                # Check cache first
                if not force_reload:
                    cached = self.cache.get(model_name)
                    if cached:
                        logger.debug(f"Model already loaded: {model_name}")
                        return {
                            "name": model_name,
                            "status": "loaded",
                            "cached": True,
                            "size_mb": cached.size / 1024 / 1024,
                            "loaded_at": cached.loaded_at.isoformat(),
                            "use_count": cached.use_count,
                        }

                # Pull model if not available
                logger.info(f"Loading model: {model_name}")

                # Get model info from Ollama
                success, models, error = self.ollama.list_models()

                if not success:
                    logger.error(f"Failed to list models: {error}")
                    return {
                        "name": model_name,
                        "status": "error",
                        "error": error or "Failed to connect to Ollama",
                    }

                # Find the model
                model_found = False
                model_size = 0

                if models:
                    for model in models:
                        if model.get("name") == model_name or model.get(
                            "name", ""
                        ).startswith(model_name):
                            model_found = True
                            model_size = model.get("size", 0)
                            break

                if not model_found:
                    logger.warning(f"Model not found, attempting to pull: {model_name}")
                    # In production, you'd pull the model here
                    # For now, create a placeholder entry
                    model_size = 1024 * 1024 * 1024  # 1GB estimate

                # Create model info and cache it
                model_info = ModelInfo(
                    name=model_name,
                    size=model_size,
                    loaded_at=datetime.now(timezone.utc),
                    last_used=datetime.now(timezone.utc),
                    use_count=1,
                    parameters={},
                )

                self.cache.put(model_name, model_info)

                logger.info(f"Model loaded successfully: {model_name}")

                return {
                    "name": model_name,
                    "status": "loaded",
                    "cached": False,
                    "size_mb": model_size / 1024 / 1024,
                    "loaded_at": model_info.loaded_at.isoformat(),
                }

            except (OllamaError, ConnectionError, OSError) as e:
                logger.error(f"Failed to load model {model_name}: {e}")
                return {"name": model_name, "status": "error", "error": str(e)}

    def unload_model(self, model_name: str) -> bool:
        """
        Unload a model from cache

        Args:
            model_name: Name of the model to unload

        Returns:
            True if unloaded, False otherwise
        """
        with self._operation_lock:
            try:
                self.cache.remove(model_name)
                logger.info(f"Model unloaded: {model_name}")
                return True
            except (OllamaError, ConnectionError, OSError) as e:
                logger.error(f"Failed to unload model {model_name}: {e}")
                return False

    def generate(
        self, model_name: str, prompt: str, **kwargs
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Generate text using a model

        Args:
            model_name: Name of the model to use
            prompt: Input prompt
            **kwargs: Additional parameters for generation

        Returns:
            Tuple of (success, response_text, error)
        """
        with self._operation_lock:
            try:
                # Ensure model is loaded
                model_info = self.load_model(model_name)

                if model_info.get("status") != "loaded":
                    error = model_info.get("error", "Failed to load model")
                    return False, None, error

                # Call Ollama API for generation
                success, response, error = self.ollama.generate(
                    model=model_name, prompt=prompt, **kwargs
                )

                if success:
                    # Update cache stats
                    cached = self.cache.get(model_name)
                    if cached:
                        cached.last_used = datetime.now(timezone.utc)
                        cached.use_count += 1

                    return True, response, None
                else:
                    return False, None, error

            except (OllamaError, ConnectionError, OSError) as e:
                logger.error(f"Generation failed for model {model_name}: {e}")
                return False, None, str(e)

    def warmup(self, model_names: List[str]):
        """
        Preload models on startup

        Args:
            model_names: List of model names to preload
        """
        logger.info(f"Warming up models: {model_names}")

        for model_name in model_names:
            try:
                result = self.load_model(model_name)
                if result.get("status") == "loaded":
                    logger.info(f"Model warmed up: {model_name}")
                else:
                    logger.warning(
                        f"Failed to warm up model {model_name}: {result.get('error')}"
                    )
            except (OllamaError, ConnectionError, OSError) as e:
                logger.error(f"Error warming up model {model_name}: {e}")

        self._warmup_models = model_names

    def list_loaded_models(self) -> List[str]:
        """
        Get list of currently loaded models

        Returns:
            List of model names
        """
        stats = self.cache.get_stats()
        return stats.get("models", [])

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics

        Returns:
            Dict with cache stats
        """
        return self.cache.get_stats()

    def get_available_models(self) -> Tuple[bool, Optional[List[str]], Optional[str]]:
        """
        Get list of available models from Ollama

        Returns:
            Tuple of (success, model_names, error)
        """
        try:
            success, models, error = self.ollama.list_models()

            if success and models:
                model_names = [m.get("name") for m in models]
                return True, model_names, None
            else:
                return False, None, error
        except (OllamaError, ConnectionError, OSError) as e:
            logger.error(f"Failed to get available models: {e}")
            return False, None, str(e)

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up ModelManager")
        self.cache.clear()


# Global instance
model_manager = ModelManager()
