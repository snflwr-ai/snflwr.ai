# utils/ollama_client.py
"""
Ollama API Client Wrapper
Simplified, robust interface for Ollama operations with comprehensive error handling
Includes circuit breaker pattern to prevent cascading failures
"""

import requests
import json
import random
from typing import Optional, Dict, List, Generator, Tuple, Callable
import time

from config import system_config
from utils.logger import get_logger, log_performance_metric
from utils.circuit_breaker import ollama_circuit, CircuitOpenError

logger = get_logger(__name__)


class OllamaError(Exception):
    """Base exception for Ollama operations"""

    pass


class OllamaConnectionError(OllamaError):
    """Cannot connect to Ollama service"""

    pass


class OllamaTimeoutError(OllamaError):
    """Operation timed out"""

    pass


class OllamaClient:
    """
    Simplified, production-ready Ollama API client
    Provides high-level interface with automatic retries and error handling.
    Includes circuit breaker to prevent cascading failures.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        use_circuit_breaker: bool = True,
    ):
        """
        Initialize Ollama client

        Args:
            host: Ollama service host URL
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            use_circuit_breaker: Whether to use circuit breaker (default: True)
        """
        self.host = host or system_config.OLLAMA_HOST
        self.timeout = timeout or system_config.OLLAMA_TIMEOUT
        self.max_retries = max_retries or system_config.OLLAMA_MAX_RETRIES
        self.base_retry_delay = (
            system_config.OLLAMA_RETRY_DELAY
        )  # Base delay for exponential backoff
        self.max_retry_delay = 30.0  # Cap the maximum delay at 30 seconds
        self.use_circuit_breaker = use_circuit_breaker
        self._circuit = ollama_circuit if use_circuit_breaker else None

        logger.info(
            f"Ollama client initialized: {self.host} (circuit breaker: {use_circuit_breaker})"
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

    def _calculate_backoff_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay with jitter.

        Uses the "decorrelated jitter" algorithm which provides better
        distribution than full jitter while avoiding thundering herd problems.

        Args:
            attempt: Current retry attempt (0-indexed)

        Returns:
            Delay in seconds before next retry
        """
        # Exponential backoff: base_delay * 2^attempt
        exponential_delay = self.base_retry_delay * (2**attempt)

        # Cap at maximum delay
        capped_delay = min(exponential_delay, self.max_retry_delay)

        # Add jitter: random value between 0 and capped_delay
        # This prevents synchronized retries from multiple clients
        jitter = random.uniform(0, capped_delay * 0.5)

        final_delay = capped_delay + jitter

        logger.debug(
            f"Retry backoff: attempt={attempt}, base={self.base_retry_delay}s, "
            f"exponential={exponential_delay:.2f}s, capped={capped_delay:.2f}s, "
            f"jitter={jitter:.2f}s, final={final_delay:.2f}s"
        )

        return final_delay

    def check_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Check if Ollama service is accessible

        Returns:
            Tuple of (is_available, version or error)
        """
        try:
            response = requests.get(f"{self.host}/api/version", timeout=5)

            if response.ok:
                data = response.json()
                version = data.get("version", "unknown")
                logger.info(f"Ollama service available: v{version}")
                return True, version
            else:
                return False, f"Service returned {response.status_code}"

        except requests.RequestException as e:
            logger.error(f"Ollama connection check failed: {e}")
            return False, str(e)

    def list_models(self) -> Tuple[bool, Optional[List[Dict]], Optional[str]]:
        """
        List all available models

        Returns:
            Tuple of (success, models or None, error or None)
        """
        try:
            response = self._request_with_retry("GET", f"{self.host}/api/tags")

            if not response:
                return False, None, "Failed to connect to Ollama"

            data = response.json()
            models = data.get("models", [])

            logger.info(f"Listed {len(models)} models")
            return True, models, None

        except (OllamaError, requests.RequestException, ConnectionError, OSError) as e:
            logger.error(f"Failed to list models: {e}")
            return False, None, str(e)

    def pull_model(
        self, model_name: str, progress_callback: Optional[Callable] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Pull a model from registry

        Args:
            model_name: Model to pull
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (success, error or None)
        """
        try:
            logger.info(f"Pulling model: {model_name}")

            chunk_timeout = min(self.timeout, 300)  # Max 5 min between chunks
            response = requests.post(
                f"{self.host}/api/pull",
                json={"name": model_name},
                timeout=(self.timeout, chunk_timeout),  # (connect, read-per-chunk)
                stream=True,
            )

            if not response.ok:
                return False, f"Pull failed: {response.status_code}"

            # Stream progress
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)

                    if progress_callback:
                        progress_callback(data)

                    if "error" in data:
                        return False, data["error"]

                    status = data.get("status", "")
                    if status:
                        logger.debug(f"Pull: {status}")

            logger.info(f"Model pulled successfully: {model_name}")
            return True, None

        except requests.Timeout:
            return False, "Pull operation timed out"
        except (OllamaError, requests.RequestException, ConnectionError, OSError) as e:
            logger.error(f"Failed to pull model: {e}")
            return False, str(e)

    def create_model(
        self,
        model_name: str,
        modelfile: str,
        progress_callback: Optional[Callable] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Create custom model from modelfile

        Args:
            model_name: Name for new model
            modelfile: Modelfile content
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (success, error or None)
        """
        try:
            logger.info(f"Creating model: {model_name}")

            chunk_timeout = min(self.timeout, 300)
            response = requests.post(
                f"{self.host}/api/create",
                json={"name": model_name, "modelfile": modelfile},
                timeout=(self.timeout, chunk_timeout),  # (connect, read-per-chunk)
                stream=True,
            )

            if not response.ok:
                return False, f"Creation failed: {response.status_code}"

            # Stream progress
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)

                    if progress_callback:
                        progress_callback(data)

                    if "error" in data:
                        return False, data["error"]

                    status = data.get("status", "")
                    if status:
                        logger.debug(f"Create: {status}")

            logger.info(f"Model created successfully: {model_name}")
            return True, None

        except requests.Timeout:
            return False, "Creation operation timed out"
        except (OllamaError, requests.RequestException, ConnectionError, OSError) as e:
            logger.error(f"Failed to create model: {e}")
            return False, str(e)

    def generate(
        self,
        model: str,
        prompt: str,
        context: Optional[List] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Generate response from model

        Args:
            model: Model name
            prompt: Input prompt
            context: Optional conversation context
            options: Optional model parameters
            stream: Whether to stream response

        Returns:
            Tuple of (success, response or None, metadata or None)
        """
        try:
            request_data = {"model": model, "prompt": prompt, "stream": stream}

            if context:
                request_data["context"] = context

            if options:
                request_data["options"] = options

            start_time = time.time()

            # Make request with retry
            response = self._request_with_retry(
                "POST",
                f"{self.host}/api/generate",
                json_data=request_data,
                stream=stream,
            )

            if not response:
                return False, None, {"error": "Failed to connect"}

            if stream:
                # Return streaming generator
                def stream_generator():
                    for line in response.iter_lines():
                        if line:
                            data = json.loads(line)
                            if "response" in data:
                                yield data["response"]
                            if data.get("done", False):
                                break

                return True, stream_generator(), None
            else:
                # Parse complete response
                result = response.json()

                response_time = time.time() - start_time
                log_performance_metric(
                    "ollama_generate_time", response_time * 1000, "ms"
                )

                metadata = {
                    "model": model,
                    "response_time_ms": int(response_time * 1000),
                    "context": result.get("context"),
                    "total_duration": result.get("total_duration"),
                    "load_duration": result.get("load_duration"),
                    "prompt_eval_count": result.get("prompt_eval_count"),
                    "eval_count": result.get("eval_count"),
                }

                return True, result.get("response", ""), metadata

        except (OllamaError, requests.RequestException, ConnectionError, OSError) as e:
            logger.error(f"Generation failed: {e}")
            return False, None, {"error": str(e)}

    def chat(
        self,
        model: str,
        messages: List[Dict],
        options: Optional[Dict] = None,
        stream: bool = False,
        think: Optional[bool] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Chat with model using message history

        Args:
            model: Model name
            messages: List of message dictionaries
            options: Optional model parameters
            stream: Whether to stream response
            think: Enable/disable chain-of-thought thinking (Qwen3 models).
                   Pass False to disable thinking mode and get faster responses.

        Returns:
            Tuple of (success, response or None, metadata or None)
        """
        try:
            request_data = {"model": model, "messages": messages, "stream": stream}

            if options:
                request_data["options"] = options

            if think is not None:
                request_data["think"] = think

            start_time = time.time()

            response = self._request_with_retry(
                "POST", f"{self.host}/api/chat", json_data=request_data, stream=stream
            )

            if not response:
                return False, None, {"error": "Failed to connect"}

            if stream:
                # Return streaming generator
                def stream_generator():
                    for line in response.iter_lines():
                        if line:
                            data = json.loads(line)
                            if "message" in data and "content" in data["message"]:
                                yield data["message"]["content"]
                            if data.get("done", False):
                                break

                return True, stream_generator(), None
            else:
                result = response.json()

                response_time = time.time() - start_time
                log_performance_metric("ollama_chat_time", response_time * 1000, "ms")

                message = result.get("message", {})
                response_text = message.get("content", "")

                metadata = {
                    "model": model,
                    "response_time_ms": int(response_time * 1000),
                    "total_duration": result.get("total_duration"),
                    "load_duration": result.get("load_duration"),
                    "prompt_eval_count": result.get("prompt_eval_count"),
                    "eval_count": result.get("eval_count"),
                }

                return True, response_text, metadata

        except (OllamaError, requests.RequestException, ConnectionError, OSError) as e:
            logger.error(f"Chat failed: {e}")
            return False, None, {"error": str(e)}

    def delete_model(self, model_name: str) -> Tuple[bool, Optional[str]]:
        """
        Delete a model

        Args:
            model_name: Model to delete

        Returns:
            Tuple of (success, error or None)
        """
        try:
            response = self._request_with_retry(
                "DELETE", f"{self.host}/api/delete", json_data={"name": model_name}
            )

            if response:
                logger.info(f"Model deleted: {model_name}")
                return True, None
            else:
                return False, "Failed to delete model"

        except (OllamaError, requests.RequestException, ConnectionError, OSError) as e:
            logger.error(f"Failed to delete model: {e}")
            return False, str(e)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict] = None,
        stream: bool = False,
    ) -> Optional[requests.Response]:
        """
        Make HTTP request with automatic retry and circuit breaker protection.

        Args:
            method: HTTP method
            url: Request URL
            json_data: Optional JSON payload
            stream: Whether to stream response

        Returns:
            Response object or None if all retries failed

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

        last_error = None
        for attempt in range(self.max_retries):
            try:
                if method == "GET":
                    response = requests.get(url, timeout=self.timeout)
                elif method == "POST":
                    response = requests.post(
                        url, json=json_data, timeout=self.timeout, stream=stream
                    )
                elif method == "DELETE":
                    response = requests.delete(
                        url, json=json_data, timeout=self.timeout
                    )
                else:
                    logger.error(f"Unsupported method: {method}")
                    return None

                if response.ok:
                    # Record success with circuit breaker
                    if self._circuit:
                        self._circuit.record_success()
                    return response

                logger.warning(
                    f"Request attempt {attempt + 1}/{self.max_retries} failed: HTTP {response.status_code}"
                )
                last_error = Exception(f"HTTP {response.status_code}")

                if attempt < self.max_retries - 1:
                    backoff_delay = self._calculate_backoff_delay(attempt)
                    logger.info(
                        f"Retrying in {backoff_delay:.2f}s (attempt {attempt + 2}/{self.max_retries})"
                    )
                    time.sleep(backoff_delay)

            except requests.Timeout as e:
                logger.warning(
                    f"Request attempt {attempt + 1}/{self.max_retries} timed out after {self.timeout}s"
                )
                last_error = e
                if attempt < self.max_retries - 1:
                    backoff_delay = self._calculate_backoff_delay(attempt)
                    logger.info(
                        f"Retrying in {backoff_delay:.2f}s (attempt {attempt + 2}/{self.max_retries})"
                    )
                    time.sleep(backoff_delay)
            except requests.RequestException as e:
                logger.warning(
                    f"Request attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                last_error = e
                if attempt < self.max_retries - 1:
                    backoff_delay = self._calculate_backoff_delay(attempt)
                    logger.info(
                        f"Retrying in {backoff_delay:.2f}s (attempt {attempt + 2}/{self.max_retries})"
                    )
                    time.sleep(backoff_delay)

        # All retries exhausted - record failure with circuit breaker
        logger.error("All retry attempts failed")
        if self._circuit:
            self._circuit.record_failure(last_error)

        return None


# Default client instance
ollama_client = OllamaClient()


# Export public interface
__all__ = [
    "OllamaClient",
    "OllamaError",
    "OllamaConnectionError",
    "OllamaTimeoutError",
    "CircuitOpenError",
    "ollama_client",
]
