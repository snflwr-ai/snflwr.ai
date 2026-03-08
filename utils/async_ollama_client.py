"""
Async Ollama Client
High-performance asynchronous client for Ollama API
Enables non-blocking AI inference for better concurrency
Includes circuit breaker pattern to prevent cascading failures
"""

import asyncio
import aiohttp
from typing import Dict, Any, Optional, Tuple
import json
from datetime import datetime

from config import system_config
from utils.logger import get_logger, log_performance_metric
from utils.circuit_breaker import ollama_circuit, CircuitOpenError
from utils.ollama_client import OllamaError

logger = get_logger(__name__)


class AsyncOllamaClient:
    """
    Asynchronous Ollama API client with connection pooling.
    Significantly improves throughput for concurrent requests.
    Includes circuit breaker to prevent cascading failures.
    """

    def __init__(
        self,
        base_url: str = None,
        timeout: int = None,
        max_retries: int = 3,
        max_connections: int = 100,
        use_circuit_breaker: bool = True,
    ):
        """
        Initialize async Ollama client

        Args:
            base_url: Ollama API URL
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            max_connections: Maximum concurrent connections
            use_circuit_breaker: Whether to use circuit breaker (default: True)
        """
        self.base_url = base_url or system_config.OLLAMA_HOST
        self.timeout = timeout or system_config.OLLAMA_TIMEOUT
        self.max_retries = max_retries
        self.use_circuit_breaker = use_circuit_breaker
        self._circuit = ollama_circuit if use_circuit_breaker else None

        # Connection pooling for better performance
        connector = aiohttp.TCPConnector(
            limit=max_connections, limit_per_host=50, ttl_dns_cache=300
        )

        self.session: Optional[aiohttp.ClientSession] = None
        self.connector = connector

        logger.info(
            f"Async Ollama client initialized: {self.base_url} (circuit breaker: {use_circuit_breaker})"
        )

    @property
    def circuit_state(self) -> str:
        """Get current circuit breaker state"""
        if self._circuit:
            return self._circuit.state.value
        return "disabled"

    @property
    def circuit_stats(self) -> dict:
        """Get circuit breaker statistics"""
        if self._circuit:
            return self._circuit.get_stats()
        return {"status": "disabled"}

    async def __aenter__(self):
        """Context manager entry"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.close()

    async def initialize(self):
        """Initialize HTTP session"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(
                connector=self.connector, timeout=timeout, raise_for_status=False
            )
            logger.info("Async HTTP session created")

    async def close(self):
        """Close HTTP session and connector"""
        if self.session:
            await self.session.close()
            self.session = None
            # Connector is closed by session.close(), create a fresh one for reuse
            self.connector = aiohttp.TCPConnector(
                limit=self.connector.limit,
                limit_per_host=self.connector._limit_per_host,
                ttl_dns_cache=300,
            )
            logger.info("Async HTTP session closed")

    async def generate(
        self,
        model: str,
        prompt: str,
        system: str = None,
        options: Dict[str, Any] = None,
        stream: bool = False,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Generate completion asynchronously with circuit breaker protection.

        Args:
            model: Model name (e.g., "qwen3.5:9b")
            prompt: User prompt
            system: System prompt (optional)
            options: Model options (temperature, etc.)
            stream: Whether to stream response

        Returns:
            Tuple of (success, response_text, error_message)

        Raises:
            CircuitOpenError: If circuit breaker is open
        """
        # Check circuit breaker before attempting request
        if self._circuit and not self._circuit.can_execute():
            retry_in = self._circuit.time_until_retry()
            logger.warning(
                f"Ollama circuit breaker is OPEN. "
                f"Failing fast. Retry in {retry_in:.1f}s"
            )
            raise CircuitOpenError("ollama", retry_in)

        if not self.session:
            await self.initialize()

        start_time = asyncio.get_running_loop().time()
        last_error = None

        try:
            # Build request payload
            payload = {"model": model, "prompt": prompt, "stream": stream}

            if system:
                payload["system"] = system

            if options:
                payload["options"] = options

            # Make request with retries
            for attempt in range(self.max_retries):
                try:
                    async with self.session.post(
                        f"{self.base_url}/api/generate", json=payload
                    ) as response:

                        if response.status == 200:
                            data = await response.json()
                            response_text = data.get("response", "")

                            # Log performance
                            elapsed_ms = (
                                asyncio.get_running_loop().time() - start_time
                            ) * 1000
                            log_performance_metric(
                                "ollama_generate_time", elapsed_ms, "ms"
                            )

                            logger.info(
                                f"Ollama generate success: {model} "
                                f"({elapsed_ms:.0f}ms, {len(response_text)} chars)"
                            )

                            # Record success with circuit breaker
                            if self._circuit:
                                self._circuit.record_success()

                            return True, response_text, None

                        else:
                            error_text = await response.text()
                            last_error = Exception(f"HTTP {response.status}")
                            logger.warning(
                                f"Ollama API error (attempt {attempt + 1}/{self.max_retries}): "
                                f"Status {response.status}, {error_text}"
                            )

                            if attempt < self.max_retries - 1:
                                await asyncio.sleep(2**attempt)  # Exponential backoff
                                continue

                            # All retries failed - record with circuit breaker
                            if self._circuit:
                                self._circuit.record_failure(last_error)
                            return False, None, f"HTTP {response.status}: {error_text}"

                except asyncio.TimeoutError as e:
                    last_error = e
                    logger.warning(
                        f"Ollama timeout (attempt {attempt + 1}/{self.max_retries})"
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2**attempt)
                        continue
                    # All retries failed - record with circuit breaker
                    if self._circuit:
                        self._circuit.record_failure(last_error)
                    return False, None, "Request timeout"

                except aiohttp.ClientError as e:
                    last_error = e
                    logger.warning(
                        f"Ollama client error (attempt {attempt + 1}/{self.max_retries}): {e}"
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2**attempt)
                        continue
                    # All retries failed - record with circuit breaker
                    if self._circuit:
                        self._circuit.record_failure(last_error)
                    return False, None, f"Connection error: {str(e)}"

            # All retries exhausted
            if self._circuit:
                self._circuit.record_failure(last_error)
            return False, None, "Max retries exceeded"

        except CircuitOpenError:
            raise  # Re-raise circuit open errors
        except (
            OllamaError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"Unexpected error in Ollama generate: {e}")
            if self._circuit:
                self._circuit.record_failure(e)
            return False, None, f"Internal error: {str(e)}"

    async def chat(
        self, model: str, messages: list, options: Dict[str, Any] = None
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """
        Chat completion asynchronously

        Args:
            model: Model name
            messages: List of message dicts [{"role": "user", "content": "..."}]
            options: Model options

        Returns:
            Tuple of (success, response_dict, error_message)
        """
        if not self.session:
            await self.initialize()

        try:
            payload = {"model": model, "messages": messages, "stream": False}

            if options:
                payload["options"] = options

            async with self.session.post(
                f"{self.base_url}/api/chat", json=payload
            ) as response:

                if response.status == 200:
                    data = await response.json()
                    return True, data, None
                else:
                    error_text = await response.text()
                    return False, None, f"HTTP {response.status}: {error_text}"

        except (
            OllamaError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"Chat error: {e}")
            return False, None, str(e)

    async def list_models(self) -> Tuple[bool, Optional[list], Optional[str]]:
        """
        List available models

        Returns:
            Tuple of (success, models_list, error_message)
        """
        if not self.session:
            await self.initialize()

        try:
            async with self.session.get(f"{self.base_url}/api/tags") as response:
                if response.status == 200:
                    data = await response.json()
                    models = data.get("models", [])
                    return True, models, None
                else:
                    error_text = await response.text()
                    return False, None, f"HTTP {response.status}: {error_text}"

        except (
            OllamaError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"List models error: {e}")
            return False, None, str(e)

    async def pull_model(
        self, model: str, insecure: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Pull a model from registry

        Args:
            model: Model name to pull
            insecure: Allow insecure connections

        Returns:
            Tuple of (success, error_message)
        """
        if not self.session:
            await self.initialize()

        try:
            payload = {"name": model, "insecure": insecure}

            async with self.session.post(
                f"{self.base_url}/api/pull", json=payload
            ) as response:

                if response.status == 200:
                    # Stream progress updates
                    async for line in response.content:
                        if line:
                            progress = json.loads(line.decode())
                            status = progress.get("status", "")
                            logger.info(f"Pulling {model}: {status}")

                    return True, None
                else:
                    error_text = await response.text()
                    return False, f"HTTP {response.status}: {error_text}"

        except (
            OllamaError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"Pull model error: {e}")
            return False, str(e)

    async def health_check(self) -> bool:
        """
        Check if Ollama server is healthy

        Returns:
            True if healthy, False otherwise
        """
        if not self.session:
            await self.initialize()

        try:
            async with self.session.get(f"{self.base_url}/api/tags") as response:
                return response.status == 200
        except (
            OllamaError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def generate_batch(
        self, requests: list[Dict[str, Any]]
    ) -> list[Tuple[bool, Optional[str], Optional[str]]]:
        """
        Generate multiple completions concurrently

        Args:
            requests: List of generation request dicts

        Returns:
            List of results (success, response, error) for each request
        """
        tasks = [
            self.generate(
                model=req.get("model"),
                prompt=req.get("prompt"),
                system=req.get("system"),
                options=req.get("options"),
            )
            for req in requests
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions in results
        processed_results: list = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Batch generation error: {result}")
                processed_results.append((False, None, str(result)))
            else:
                processed_results.append(result)

        return processed_results


# Global async client instance
_async_ollama_client: Optional[AsyncOllamaClient] = None


async def get_async_ollama_client() -> AsyncOllamaClient:
    """
    Get or create global async Ollama client

    Returns:
        AsyncOllamaClient instance
    """
    global _async_ollama_client

    if _async_ollama_client is None:
        _async_ollama_client = AsyncOllamaClient()
        await _async_ollama_client.initialize()

    return _async_ollama_client


async def close_async_ollama_client():
    """Close global async Ollama client"""
    global _async_ollama_client

    if _async_ollama_client:
        await _async_ollama_client.close()
        _async_ollama_client = None


# Export public interface
__all__ = ["AsyncOllamaClient", "get_async_ollama_client", "close_async_ollama_client"]
