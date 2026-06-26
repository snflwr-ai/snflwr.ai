"""Stage 4 semantic classifier (Ollama LLM).

Extracted verbatim from the former monolithic safety/pipeline.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from config import safety_config
from safety.pipeline.models import Category, SafetyResult, Severity, _block
from utils.logger import get_logger

logger = get_logger(__name__)


class _SemanticClassifier:
    """
    LLM-based semantic safety classifier using a local Ollama model.

    If Ollama is unavailable at init time the classifier marks itself as
    unavailable and ``classify()`` returns None (skip). The deterministic
    stages (1-3, 5) still protect.  If Ollama errors *during* a classify
    call the result is BLOCK (fail closed).
    """

    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self) -> None:
        self._available = False
        self._model: Optional[str] = None
        self._client = None
        self._state = "disabled"  # "available", "degraded", "disabled"
        self._state_since = datetime.now(timezone.utc)
        self._probe_task = None

        try:
            from utils.ollama_client import (
                OllamaClient as _OllamaClient,
            )  # noqa: F811
            from utils.ollama_client import (
                OllamaError as _OE,
            )

            self._client = _OllamaClient(timeout=45, max_retries=1)
            self._OllamaError = _OE

            ok, _version = self._client.check_connection()
            if not ok:
                logger.warning(
                    "Ollama not reachable at init; semantic classifier disabled."
                )
                return

            self._model = self._find_model()
            if self._model:
                self._transition_state("available")
                logger.info("Semantic classifier ready (model=%s)", self._model)
            else:
                logger.warning(
                    "No suitable safety model found; semantic classifier disabled."
                )

        except ImportError:
            logger.warning(
                "ollama_client not importable; semantic classifier disabled."
            )
        except Exception as exc:  # Intentional: init must not crash
            logger.warning("Semantic classifier init failed: %s", exc)

    def _find_model(self) -> Optional[str]:
        """Find the best available safety model from preferred + fallbacks."""
        preferred = getattr(safety_config, "SAFETY_MODEL", "llama-guard3:8b")
        fallbacks = getattr(
            safety_config, "SAFETY_MODEL_FALLBACKS", ["llama-guard3:1b"]
        )
        success, models, _err = self._client.list_models()
        if success and models:
            names = [m.get("name", "") for m in models]
            if preferred in names:
                return preferred
            for fb in fallbacks:
                if fb in names:
                    return fb
        return None

    def _transition_state(self, new_state: str) -> None:
        """Transition classifier state with logging and alerting."""
        old_state = getattr(self, "_state", "disabled")
        if old_state == new_state:
            return

        self._state = new_state
        self._state_since = datetime.now(timezone.utc)

        if new_state == "available":
            self._available = True
            logger.info("Safety classifier state: %s -> available", old_state)
            try:
                from core.email_service import email_service

                email_service.send_operator_alert(
                    subject="Safety classifier recovered",
                    description=(
                        f"Semantic classification re-enabled (was {old_state}). "
                        f"Model: {self._model}"
                    ),
                )
            except Exception:
                pass  # Alert is best-effort
        else:
            self._available = False
            logger.warning("Safety classifier state: %s -> %s", old_state, new_state)
            if old_state == "available":
                try:
                    from core.email_service import email_service

                    email_service.send_operator_alert(
                        subject="Safety classifier degraded",
                        description=(
                            "Semantic classifier lost Ollama connection. "
                            "Deterministic safety stages (1-3, 5) still protecting. "
                            "Auto-recovery probing every 60s."
                        ),
                    )
                except Exception:
                    pass

    def alert_if_unavailable(self) -> None:
        """Operator-alert if the classifier is not available. Best-effort.

        Covers the SILENT case the runtime transitions miss: a classifier that
        *starts* disabled (safety model never pulled, Ollama unreachable at
        init, import error) never transitions from "available", so the
        degraded-path alert in ``_transition_state`` never fires. Call once at
        startup so a degraded safety posture is loud, not silent.
        """
        if self._state == "available":
            return
        logger.warning(
            "Safety classifier is %s at startup — only deterministic stages protect.",
            self._state,
        )
        try:
            from core.email_service import email_service

            email_service.send_operator_alert(
                subject="Safety classifier DISABLED",
                description=(
                    f"The semantic safety classifier is '{self._state}' at startup. "
                    "Likely cause: the safety model (llama-guard) is not pulled, or "
                    "Ollama was unreachable at init. Deterministic safety stages "
                    "(1-3, 5) still protect, but the ML layer is OFF. Run "
                    "./deploy.sh to pull the safety model, or check Ollama."
                ),
            )
        except Exception:
            pass  # alert is best-effort; never raise at startup

    def _probe_ollama(self) -> bool:
        """Lightweight health check: is Ollama reachable with a safety model?"""
        if self._client is None:
            return False
        try:
            ok, _version = self._client.check_connection()
            if not ok:
                return False
            model = self._find_model()
            if model:
                self._model = model
                return True
            return False
        except Exception:
            return False

    async def run_health_probe(self) -> None:
        """Background task: probe Ollama periodically, auto-recover."""
        import asyncio

        while True:
            try:
                interval = 60 if self._state in ("degraded", "disabled") else 300
                await asyncio.sleep(interval)

                loop = asyncio.get_event_loop()
                healthy = await loop.run_in_executor(None, self._probe_ollama)
                if healthy and self._state != "available":
                    self._transition_state("available")
                elif not healthy and self._state == "available":
                    self._transition_state("degraded")
                elif not healthy:
                    logger.debug("Classifier probe: still %s", self._state)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Classifier probe error: %s", exc)

    # --------------------------------------------------------------------- #

    def classify(self, text: str, age: Optional[int] = None) -> Optional[SafetyResult]:
        """
        Classify text via the Ollama safety model.

        Returns:
            SafetyResult on block, None to continue.

        Fail-closed-on-unavailable policy (finding F2): when the classifier is
        unavailable (e.g. safety model not loaded at init), the ML layer would
        otherwise be silently skipped. We fail CLOSED. SAFETY_CLASSIFIER_REQUIRED
        defaults True, so teens AND unknown-age block too — every user is a minor
        and the safety model ships in every stack, so an unavailable classifier
        is a broken safety layer, not a normal config. Under-13 always fails
        closed regardless of the flag. A deployment intentionally running without
        the safety model must set SAFETY_CLASSIFIER_REQUIRED=false to opt out, in
        which case teens/unknown-age degrade to deterministic stages only. The
        operator is alerted regardless (alert_if_unavailable / state transitions).
        """
        if not self._available or self._client is None:
            try:
                from config import system_config

                # Fail CLOSED if the attr is missing: the default must match the
                # config default (True), not diverge to open. A malformed config
                # must not silently degrade teen/unknown-age safety.
                require = getattr(system_config, "SAFETY_CLASSIFIER_REQUIRED", True)
            except Exception:
                require = True  # config unavailable -> fail closed for ALL ages
            if require or (age is not None and age < 13):
                logger.warning(
                    "Safety classifier unavailable — failing closed (age=%s, "
                    "required=%s).",
                    age,
                    require,
                )
                return _block(
                    Severity.MAJOR,
                    Category.CLASSIFIER_ERROR,
                    "Safety check is temporarily unavailable (fail closed).",
                    stage="classifier",
                )
            return None  # teen/unknown, not strict -- deterministic stages protect

        try:
            prompt = self._build_prompt(text, age)
            success, response, _meta = self._client.generate(
                model=self._model,
                prompt=prompt,
                options={"temperature": 0.0, "num_predict": 250},
            )

            if not success or response is None:
                logger.error("Ollama generation failed; failing closed.")
                self._transition_state("degraded")
                return _block(
                    Severity.MAJOR,
                    Category.CLASSIFIER_ERROR,
                    "Semantic classifier generation failed (fail closed).",
                    stage="classifier",
                )

            return self._parse_response(response)

        except Exception as exc:  # Intentional catch-all: fail closed
            logger.error(
                "Stage 4 (classifier) error, failing closed: %s", exc, exc_info=True
            )
            self._transition_state("degraded")
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Semantic classifier error (fail closed).",
                stage="classifier",
            )

    # --------------------------------------------------------------------- #

    @staticmethod
    def _build_prompt(text: str, age: Optional[int]) -> str:
        age_ctx = f"Student age: {age}" if age else "Age unknown"
        return (
            "You are a content safety classifier for a K-12 educational platform "
            "(COPPA/FERPA compliant). Classify the following student message.\n\n"
            f"{age_ctx}\n\n"
            f'Message: "{text}"\n\n'
            "Categories: violence, self_harm, exploitation, sexual, drugs, weapons, "
            "pii, bullying, bypass_attempt, age_inappropriate, acceptable\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            '{"safe": true/false, "category": "...", "confidence": 0.0-1.0, '
            '"reason": "brief explanation"}'
        )

    # Llama Guard 3 category mapping (S1-S13 codes)
    _GUARD_CATEGORIES = {
        "S1": Category.VIOLENCE,
        "S2": Category.SEXUAL,
        "S3": Category.WEAPONS,
        "S4": Category.WEAPONS,
        "S5": Category.SELF_HARM,
        "S6": Category.EXPLOITATION,
        "S7": Category.EXPLOITATION,
        "S8": Category.PII,
        "S9": Category.BYPASS_ATTEMPT,
        "S10": Category.DRUGS,
        "S11": Category.BULLYING,
        "S12": Category.VIOLENCE,
        "S13": Category.AGE_INAPPROPRIATE,
    }

    def _parse_response(self, raw: str) -> Optional[SafetyResult]:
        """Parse the LLM response. Handles both JSON and Llama Guard plain text format."""
        try:
            text = raw.strip()

            # Handle Llama Guard 3 plain text format: "safe" or "unsafe\nS1"
            text_lower = text.lower()
            if text_lower == "safe":
                return None  # pass
            if text_lower.startswith("unsafe"):
                lines = text.split("\n")
                guard_code = lines[1].strip() if len(lines) > 1 else ""
                category = self._GUARD_CATEGORIES.get(guard_code, Category.VIOLENCE)
                return _block(
                    Severity.MAJOR,
                    category,
                    f"Llama Guard classified as unsafe ({guard_code})",
                    stage="classifier",
                    keywords=(guard_code,) if guard_code else (),
                )

            # Handle markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                json_lines: list[str] = []
                in_block = False
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block:
                        json_lines.append(line)
                text = "\n".join(json_lines)

            result = json.loads(text)

            is_safe = result.get("safe", False)
            category_str = result.get("category", "")
            confidence = float(result.get("confidence", 0.0))
            reason = result.get("reason", "")

            # If model says safe with acceptable category -> allow
            if is_safe and category_str == "acceptable":
                return None  # pass

            # Map string category to enum
            cat_map = {
                "violence": Category.VIOLENCE,
                "self_harm": Category.SELF_HARM,
                "exploitation": Category.EXPLOITATION,
                "sexual": Category.SEXUAL,
                "drugs": Category.DRUGS,
                "weapons": Category.WEAPONS,
                "pii": Category.PII,
                "bullying": Category.BULLYING,
                "bypass_attempt": Category.BYPASS_ATTEMPT,
                "age_inappropriate": Category.AGE_INAPPROPRIATE,
                "acceptable": Category.VALID,
            }
            category = cat_map.get(category_str, Category.CLASSIFIER_ERROR)

            # If not safe -> block
            if not is_safe:
                return _block(
                    Severity.MAJOR,
                    category,
                    reason or f"Classified as {category_str}",
                    stage="classifier",
                )

            # Safe but non-acceptable category with low confidence -> block
            if category != Category.VALID and confidence < self.CONFIDENCE_THRESHOLD:
                return _block(
                    Severity.MAJOR,
                    category,
                    f"Low confidence ({confidence:.2f}) on category {category_str} (fail closed).",
                    stage="classifier",
                )

            return None  # pass

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.error(
                "Failed to parse classifier response: %s (raw=%s)", exc, raw[:200]
            )
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Unparseable classifier response (fail closed).",
                stage="classifier",
            )
