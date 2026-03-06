"""
Chat API Routes
Handles chat requests with integrated safety monitoring

[LOCKED] SECURED: All routes require authentication
- Parents can only chat for their own children's profiles
- Admins can chat for any profile
"""

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, field_validator, Field
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from config import system_config
from resource_detection import get_resource_profile as _get_resource_profile
_resources = _get_resource_profile()
from utils.input_validation import (
    validate_profile_id, validate_session_id, validate_message,
    UUID_HEX_PATTERN, SESSION_TOKEN_PATTERN,
    MIN_MESSAGE_LENGTH, MAX_MESSAGE_LENGTH
)
from core.profile_manager import ProfileManager
from core.session_manager import session_manager, SessionError, SessionLimitError
from core.authentication import auth_manager, AuthSession
from api.middleware.auth import (
    get_current_session,
    VerifySessionAccess,
    audit_log
)
from safety.pipeline import safety_pipeline
from safety.safety_monitor import safety_monitor
from safety.incident_logger import incident_logger
from utils.ollama_client import ollama_client, OllamaError
from storage.db_adapters import DB_ERRORS
from storage.conversation_store import conversation_store
from utils.rate_limiter import RateLimiter
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()

# Initialize rate limiter
rate_limiter = RateLimiter()


def _get_or_create_conversation_id(session_id: str, profile_id: str) -> str:
    """Return the active conversation_id for a session, creating one if absent."""
    rows = conversation_store.db.execute_query(
        "SELECT conversation_id FROM conversations WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (session_id,)
    )
    if rows:
        row = rows[0]
        return row['conversation_id'] if isinstance(row, dict) else row[0]
    conv = conversation_store.create_conversation(
        session_id=session_id,
        profile_id=profile_id
    )
    return conv.conversation_id


def check_chat_rate_limit(request: Request):
    """Rate limit chat messages: 100 requests per 60 seconds per IP"""
    client_ip = request.client.host if request.client else "unknown"
    allowed, info = rate_limiter.check_rate_limit(
        identifier=client_ip,
        max_requests=100,
        window_seconds=60,
        limit_type="chat"
    )
    if not allowed:
        retry_after = info.get("retry_after", 60) if isinstance(info, dict) else 60
        raise HTTPException(
            status_code=429,
            detail="Too many chat requests. Please slow down.",
            headers={"Retry-After": str(retry_after)}
        )
    return info


class ChatRequest(BaseModel):
    """Chat request payload from Open WebUI middleware with validated fields"""
    message: str = Field(..., min_length=MIN_MESSAGE_LENGTH, max_length=MAX_MESSAGE_LENGTH)
    profile_id: str = Field(..., min_length=1, max_length=64)
    model: str = Field(default=system_config.OLLAMA_DEFAULT_MODEL, max_length=100)
    session_id: Optional[str] = Field(None, min_length=32, max_length=64)
    metadata: Optional[Dict[str, Any]] = None

    @field_validator('message')
    @classmethod
    def validate_message_content(cls, v: str) -> str:
        is_valid, error = validate_message(v)
        if not is_valid:
            raise ValueError(error)
        return v.strip()

    @field_validator('profile_id')
    @classmethod
    def validate_profile_id_format(cls, v: str) -> str:
        # Allow admin/middleware sentinels that are not UUIDs
        if v.startswith("no_profile_") or v.startswith("safety_required_"):
            return v
        is_valid, error = validate_profile_id(v)
        if not is_valid:
            raise ValueError(error)
        return v

    @field_validator('session_id')
    @classmethod
    def validate_session_id_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        is_valid, error = validate_session_id(v)
        if not is_valid:
            raise ValueError(error)
        return v

    @field_validator('model')
    @classmethod
    def validate_model_name(cls, v: str) -> str:
        # Basic sanitization for model name
        if not v:
            # OLLAMA_DEFAULT_MODEL may be empty when set by hardware detection at
            # runtime.  Accept it here — the chat endpoint resolves the actual
            # model from Ollama before calling the LLM.
            return v
        v = v.strip()
        # Only allow alphanumeric, hyphen, underscore, colon, and period (for model tags)
        import re
        if not re.match(r'^[a-zA-Z0-9_\-.:]+$', v):
            raise ValueError("Model name contains invalid characters")
        return v


class ChatResponse(BaseModel):
    """Chat response payload to Open WebUI middleware"""
    message: str
    blocked: bool = False
    block_reason: Optional[str] = None
    block_category: Optional[str] = None
    possible_false_positive: bool = False
    safety_metadata: Dict[str, Any] = {}
    model: str = system_config.OLLAMA_DEFAULT_MODEL
    timestamp: str
    session_id: str


@router.post("/send", response_model=ChatResponse)
async def send_chat_message(
    request: ChatRequest,
    auth_session: AuthSession = Depends(get_current_session),
    rate_limit_info: dict = Depends(check_chat_rate_limit)
):
    """
    Process chat message through 4-layer safety pipeline

    [LOCKED] SECURED: Parents can only send messages for their own children, admins can send for any child

    This is the main endpoint called by Open WebUI middleware.
    It cannot be bypassed by students.

    Layers:
    1. Keyword-based filtering (fast)
    2. LLM classification (context-aware)
    3. Ollama generation with student-tuned prompts
    4. Response validation
    """
    try:
        logger.info(f"Chat request from profile {sanitize_log_value(request.profile_id)!r}, length={len(request.message)}")

        # Get profile
        profile_manager = ProfileManager(auth_manager.db)
        profile = profile_manager.get_profile(request.profile_id)

        if not profile:
            if auth_session.role == 'admin' and request.profile_id.startswith("no_profile_"):
                # Admin testing without a configured child profile — synthesize a default profile.
                # Age 13 applies middle-school safety filters (appropriate default for admin testing).
                from core.profile_manager import ChildProfile
                profile = ChildProfile(
                    profile_id=request.profile_id,
                    parent_id=auth_session.user_id,
                    name="Admin Test",
                    age=13,
                    grade='8',
                    is_active=True,
                )
                logger.info(f"Admin {auth_session.user_id} chatting without a child profile — using synthetic test profile")
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Profile {request.profile_id} not found. Please create a child profile first."
                )

        # AUTHORIZATION: Verify user owns this profile (unless admin)
        if auth_session.role != 'admin' and profile.parent_id != auth_session.user_id:
            logger.warning(f"Access denied: {sanitize_log_value(auth_session.user_id)!r} tried to chat for profile {sanitize_log_value(request.profile_id)!r}")
            raise HTTPException(
                status_code=403,
                detail="Access denied: You can only chat for your own children's profiles"
            )

        if not profile.is_active:
            raise HTTPException(
                status_code=403,
                detail="Profile is inactive"
            )

        # Admin testing mode — ephemeral in-memory session, no history, no DB writes.
        # Only applies when admin has no child profile (no_profile_ sentinel).
        is_admin_test = auth_session.role == 'admin' and request.profile_id.startswith("no_profile_")

        # All admins bypass the safety pipeline, monitoring, and response filtering.
        skip_safety = auth_session.role == 'admin'

        # Get or create session
        # Open WebUI sends each message as an independent request, so reuse
        # any existing active session for this profile rather than failing.
        if is_admin_test:
            # Admin test: ephemeral in-memory session — no DB write needed.
            import uuid as _uuid
            from core.session_manager import Session as _Session
            session = _Session(
                session_id=_uuid.uuid4().hex,
                profile_id=request.profile_id,
                parent_id=auth_session.user_id,
                session_type='admin_test',
                started_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            session = None
            if request.session_id:
                session = session_manager.get_session(request.session_id)

            if not session:
                # Try to reuse an existing active session for this profile
                session = session_manager.get_active_session(request.profile_id)

            if not session:
                # No active session — create one
                try:
                    session = session_manager.create_session(
                        profile_id=request.profile_id,
                        parent_id=profile.parent_id,
                        session_type='student'
                    )
                except SessionLimitError as e:
                    raise HTTPException(status_code=429, detail=str(e))
                except SessionError as e:
                    raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")

        # Load prior conversation messages for multi-turn context (skipped for admin test)
        history_messages = []
        if not is_admin_test:
            try:
                conv_id = _get_or_create_conversation_id(session.session_id, request.profile_id)
                prior_messages = conversation_store.get_conversation_messages(conv_id)
                history_messages = [
                    {"role": m.role, "content": m.content}
                    for m in prior_messages
                    if not m.safety_filtered
                ]
                # Keep only the most recent messages to stay within num_ctx budget.
                # Each turn is ~2 messages; 20 messages ≈ 10 turns of context.
                history_messages = history_messages[-20:]
            except DB_ERRORS as e:
                logger.warning(f"Could not load conversation history: {e}")

        # Start monitoring if not already (skip for admins)
        parent_id = profile.parent_id
        if not skip_safety:
            safety_monitor.start_monitoring(request.profile_id, parent_id)

        # Unified safety pipeline (pattern matching + semantic classification + age gate)
        if not skip_safety:
            filter_result = safety_pipeline.check_input(
                text=request.message,
                age=profile.age,
                profile_id=request.profile_id,
            )
        else:
            filter_result = None

        if filter_result is not None and not filter_result.is_safe:
            logger.warning(f"Content filtered: {filter_result.reason}")

            # Log incident (severity needs .value since incident_logger expects string)
            incident_logger.log_incident(
                profile_id=request.profile_id,
                session_id=session.session_id,
                incident_type=filter_result.category.value,
                severity=filter_result.severity.value,
                content_snippet=request.message[:200],
                metadata={
                    "stage": filter_result.stage,
                    "triggered_keywords": list(filter_result.triggered_keywords),
                },
            )

            # Monitor and potentially alert parent
            safety_monitor.monitor_message(
                profile_id=request.profile_id,
                message=request.message,
                message_type="user",
                session_id=session.session_id
            )

            # Return safe response
            return ChatResponse(
                message=safety_pipeline.get_safe_response(filter_result),
                blocked=True,
                block_reason=filter_result.reason,
                block_category=filter_result.category.value,
                safety_metadata={
                    "triggered_keywords": list(filter_result.triggered_keywords),
                    "stage": filter_result.stage,
                    "suggested_redirection": filter_result.suggested_redirection,
                },
                possible_false_positive=filter_result.possible_false_positive,
                model=request.model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=session.session_id
            )

        # LAYER 3: Generate AI response using Ollama
        model_name = request.model
        if not model_name:
            # No model specified and OLLAMA_DEFAULT_MODEL is empty (hardware
            # detection sets it at startup).  Ask Ollama for available models.
            try:
                ok, models, _err = ollama_client.list_models()
                if ok and models:
                    model_name = models[0].get('name', '')
            except Exception:
                pass
            if not model_name:
                raise HTTPException(
                    status_code=503,
                    detail="No AI model configured. Set OLLAMA_DEFAULT_MODEL or pull a model into Ollama."
                )

        logger.info(f"Generating response with {sanitize_log_value(model_name)!r} ({len(history_messages)} prior messages in context)")

        # Build a system prompt appropriate for the audience.
        if skip_safety:
            # Admin: no educational framing, no length limits.
            system_content = "You are a helpful AI assistant. Provide complete, detailed, and accurate responses without any length restrictions."
        else:
            # Student: tailor tone, reading level, and pedagogy to the profile.
            grade_str = profile.grade if profile else "unknown"
            age = profile.age if profile else 13
            name = profile.name if profile else "Student"
            level = (profile.learning_level if profile else "adaptive") or "adaptive"

            # Map learning level to an instructional tone directive.
            level_note = {
                "beginner": "Use very simple words and short sentences. Avoid jargon.",
                "advanced": "You may use technical vocabulary and challenge the student with deeper explanations.",
            }.get(level, "Match your vocabulary and depth to the student's grade level.")

            system_content = (
                f"You are snflwr-ai, a friendly and encouraging AI tutor for K-12 students. "
                f"You are talking with {name}, a {age}-year-old in grade {grade_str}. "
                f"{level_note} "
                f"Always be positive, patient, and supportive. "
                f"When explaining concepts, break them into clear steps and use relatable examples. "
                f"When asked for code, always include the complete, working code in properly formatted code blocks (```language ... ```) and explain what each part does. "
                f"Keep responses focused and educational. Never produce harmful, adult, or off-topic content."
            )

        messages = (
            [{"role": "system", "content": system_content}]
            + history_messages
            + [{"role": "user", "content": request.message}]
        )
        success, response_text, metadata = ollama_client.chat(
            model=model_name,
            messages=messages,
            options={
                'temperature': 0.7,
                'num_predict': _resources.num_predict,
                'num_ctx': _resources.num_ctx,
            },
            think=False,
        )

        if not success:
            err_msg = metadata.get('error', 'unknown error') if metadata else 'unknown error'
            logger.error(f"Ollama chat failed: {sanitize_log_value(err_msg)!r}")
            raise HTTPException(
                status_code=503,
                detail=f"AI model unavailable: {err_msg}"
            )

        # Strip thinking tokens — qwen3.5 embeds <think>...</think> blocks in
        # message.content. The Snflwr API returns a non-streaming response so
        # Open WebUI cannot collapse them into a "Thought for X seconds" section;
        # they render as italic inline text instead. Remove them here.
        import re as _re
        if len(response_text) <= 100_000:
            response_text = _re.sub(r'<think>[^<]*(?:<(?!/think>)[^<]*)*</think>', '', response_text, flags=_re.DOTALL).strip()
        else:
            response_text = response_text.strip()

        if not response_text:
            logger.error("Model returned empty response after stripping thinking tokens")
            raise HTTPException(
                status_code=503,
                detail="AI model unavailable: empty response"
            )

        # Response validation via safety pipeline (skipped for admins)
        if not skip_safety:
            response_filter = safety_pipeline.check_output(
                text=response_text,
                age=profile.age,
                profile_id=request.profile_id,
            )

            if not response_filter.is_safe:
                logger.error(f"AI generated unsafe content: {response_filter.reason}")

                # Log critical incident - AI bypassed safety
                incident_logger.log_incident(
                    profile_id=request.profile_id,
                    session_id=session.session_id,
                    incident_type="unsafe_ai_output",
                    severity=response_filter.severity.value,
                    content_snippet=response_text[:200],
                    metadata={
                        "stage": response_filter.stage,
                        "original_query": request.message[:100],
                    }
                )

                # Use safe alternative from pipeline
                response_text = response_filter.modified_content or safety_pipeline.get_safe_response(response_filter)
        else:
            response_filter = None
            audit_log('chat_admin', 'message', request.profile_id, auth_session)

        # Store messages in conversation history (skipped for admin test)
        if not is_admin_test:
            try:
                conversation_id = _get_or_create_conversation_id(session.session_id, request.profile_id)

                conversation_store.add_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=request.message,
                    tokens_used=len(request.message.split()),
                    safety_filtered=False
                )

                conversation_store.add_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=response_text,
                    model_used=model_name,
                    tokens_used=len(response_text.split()),
                    safety_filtered=response_filter is not None and not response_filter.is_safe
                )
            except DB_ERRORS as e:
                logger.warning(f"Failed to store conversation messages: {e}")

        # Monitor for patterns (skipped for admins)
        if not skip_safety:
            safety_monitor.monitor_message(
                profile_id=request.profile_id,
                message=response_text,
                message_type="assistant",
                session_id=session.session_id
            )

        # Audit log — admin requests are already logged above with 'chat_admin' scope;
        # only log the generic 'chat' event for non-admin (student) requests.
        if not skip_safety:
            audit_log('chat', 'message', request.profile_id, auth_session)

        # Return successful response
        return ChatResponse(
            message=response_text,
            blocked=False,
            safety_metadata={
                "filter_layers_passed": ["keyword", "llm_classifier", "response_validation"],
                "model_used": model_name
            },
            model=model_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session.session_id
        )

    except HTTPException:
        raise
    except SessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OllamaError as e:
        logger.error(f"Ollama error during chat: {e}")
        raise HTTPException(status_code=503, detail="AI model service temporarily unavailable")
    except DB_ERRORS as e:
        logger.error(f"Database error during chat: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during chat: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/end-session")
async def end_session(
    session_id: str,
    auth_session: AuthSession = Depends(VerifySessionAccess)
):
    """
    End a conversation session

    [LOCKED] SECURED: Parents can only end their own children's sessions, admins can end any
    """
    try:
        # Session ownership verified by VerifySessionAccess dependency
        success = session_manager.end_session(session_id)

        if not success:
            raise HTTPException(status_code=400, detail="Failed to end session")

        # Audit log
        audit_log('end', 'session', session_id, auth_session)

        return {"status": "success", "message": "Session ended"}

    except HTTPException:
        raise
    except SessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error ending session: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error ending session: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
