"""
Tests for api/middleware/csrf_protection.py — CSRF Double-Submit Cookie

Covers:
    - generate_csrf_token: token generation
    - sign_csrf_token / verify_csrf_token: HMAC signing round-trip
    - validate_csrf_token: full request validation (exempt paths, missing tokens, mismatches)
    - set_csrf_cookie: cookie setting
    - get_csrf_token_for_template: template token retrieval
    - extract_csrf_token_from_request: header and form extraction
"""

import hmac as _hmac
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.middleware.csrf_protection import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_SECRET,
    generate_csrf_token,
    sign_csrf_token,
    verify_csrf_token,
    set_csrf_cookie,
    get_csrf_token_for_template,
)


# ==========================================================================
# Token Generation
# ==========================================================================

class TestGenerateToken:

    def test_returns_hex_string(self):
        token = generate_csrf_token()
        int(token, 16)  # should not raise

    def test_correct_length(self):
        token = generate_csrf_token()
        assert len(token) == 64  # 32 bytes * 2 hex chars

    def test_unique(self):
        tokens = {generate_csrf_token() for _ in range(20)}
        assert len(tokens) == 20


# ==========================================================================
# Token Signing and Verification
# ==========================================================================

class TestTokenSigning:

    def test_sign_produces_dot_format(self):
        token = generate_csrf_token()
        signed = sign_csrf_token(token)
        assert '.' in signed
        parts = signed.split('.')
        assert len(parts) == 2

    def test_verify_valid_token(self):
        token = generate_csrf_token()
        signed = sign_csrf_token(token)
        assert verify_csrf_token(signed) is True

    def test_verify_tampered_signature(self):
        token = generate_csrf_token()
        signed = sign_csrf_token(token)
        # Flip last character of signature
        tampered = signed[:-1] + ('a' if signed[-1] != 'a' else 'b')
        assert verify_csrf_token(tampered) is False

    def test_verify_tampered_token(self):
        token = generate_csrf_token()
        signed = sign_csrf_token(token)
        # Replace token part
        parts = signed.split('.')
        tampered = "0000" + parts[0][4:] + '.' + parts[1]
        assert verify_csrf_token(tampered) is False

    def test_verify_no_dot(self):
        assert verify_csrf_token("nodothere") is False

    def test_verify_empty_string(self):
        assert verify_csrf_token("") is False

    def test_signature_uses_hmac_sha256(self):
        token = generate_csrf_token()
        expected_sig = _hmac.new(
            CSRF_SECRET.encode('utf-8'),
            token.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        signed = sign_csrf_token(token)
        actual_sig = signed.split('.')[1]
        assert actual_sig == expected_sig


# ==========================================================================
# extract_csrf_token_from_request
# ==========================================================================

class TestExtractToken:

    @pytest.mark.asyncio
    async def test_from_header(self):
        from api.middleware.csrf_protection import extract_csrf_token_from_request
        request = MagicMock()
        request.headers = {CSRF_HEADER_NAME: "my-token"}
        result = await extract_csrf_token_from_request(request)
        assert result == "my-token"

    @pytest.mark.asyncio
    async def test_from_form(self):
        from api.middleware.csrf_protection import extract_csrf_token_from_request
        request = MagicMock()
        request.headers = {}

        async def mock_form():
            return {"csrf_token": "form-token"}
        request.form = mock_form

        result = await extract_csrf_token_from_request(request)
        assert result == "form-token"

    @pytest.mark.asyncio
    async def test_no_token(self):
        from api.middleware.csrf_protection import extract_csrf_token_from_request
        request = MagicMock()
        request.headers = {}

        async def mock_form():
            return {}
        request.form = mock_form

        result = await extract_csrf_token_from_request(request)
        assert result is None


# ==========================================================================
# validate_csrf_token
# ==========================================================================

class TestValidateCsrfToken:

    @pytest.mark.asyncio
    async def test_get_request_skipped(self):
        from api.middleware.csrf_protection import validate_csrf_token
        request = MagicMock()
        request.method = "GET"
        result = await validate_csrf_token(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_exempt_path_login(self):
        from api.middleware.csrf_protection import validate_csrf_token
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/auth/login"
        result = await validate_csrf_token(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_exempt_path_register(self):
        from api.middleware.csrf_protection import validate_csrf_token
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/auth/register"
        result = await validate_csrf_token(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_exempt_internal(self):
        from api.middleware.csrf_protection import validate_csrf_token
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/internal/something"
        result = await validate_csrf_token(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_cookie_raises(self):
        from api.middleware.csrf_protection import validate_csrf_token
        from fastapi import HTTPException
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/profiles/create"
        request.cookies = {}
        with pytest.raises(HTTPException) as exc_info:
            await validate_csrf_token(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_request_token_raises(self):
        from api.middleware.csrf_protection import validate_csrf_token
        from fastapi import HTTPException

        token = generate_csrf_token()
        signed = sign_csrf_token(token)

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/profiles/create"
        request.cookies = {CSRF_COOKIE_NAME: signed}
        request.headers = {}

        async def mock_form():
            return {}
        request.form = mock_form

        with pytest.raises(HTTPException) as exc_info:
            await validate_csrf_token(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_double_submit(self):
        from api.middleware.csrf_protection import validate_csrf_token

        token = generate_csrf_token()
        signed = sign_csrf_token(token)

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/profiles/create"
        request.cookies = {CSRF_COOKIE_NAME: signed}
        request.headers = {CSRF_HEADER_NAME: signed}

        result = await validate_csrf_token(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_token_mismatch_raises(self):
        from api.middleware.csrf_protection import validate_csrf_token
        from fastapi import HTTPException

        token1 = generate_csrf_token()
        token2 = generate_csrf_token()
        signed1 = sign_csrf_token(token1)
        signed2 = sign_csrf_token(token2)

        request = MagicMock()
        request.method = "DELETE"
        request.url.path = "/api/profiles/delete"
        request.cookies = {CSRF_COOKIE_NAME: signed1}
        request.headers = {CSRF_HEADER_NAME: signed2}

        with pytest.raises(HTTPException) as exc_info:
            await validate_csrf_token(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_cookie_signature_raises(self):
        from api.middleware.csrf_protection import validate_csrf_token
        from fastapi import HTTPException

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/profiles/create"
        request.cookies = {CSRF_COOKIE_NAME: "bad.signature"}
        request.headers = {CSRF_HEADER_NAME: "bad.signature"}

        with pytest.raises(HTTPException) as exc_info:
            await validate_csrf_token(request)
        assert exc_info.value.status_code == 403


# ==========================================================================
# set_csrf_cookie
# ==========================================================================

class TestSetCsrfCookie:

    def test_sets_cookie_on_response(self):
        response = MagicMock()
        signed = set_csrf_cookie(response)
        response.set_cookie.assert_called_once()
        kwargs = response.set_cookie.call_args.kwargs
        assert kwargs["key"] == CSRF_COOKIE_NAME
        assert kwargs["value"] == signed
        assert kwargs["httponly"] is False  # Must be accessible to JS
        assert kwargs["samesite"] == "strict"

    def test_generates_new_token_if_none(self):
        response = MagicMock()
        signed = set_csrf_cookie(response)
        assert '.' in signed
        assert verify_csrf_token(signed) is True

    def test_uses_provided_token(self):
        response = MagicMock()
        raw_token = generate_csrf_token()
        signed = set_csrf_cookie(response, token=raw_token)
        # Token part should match
        assert signed.startswith(raw_token + ".")


# ==========================================================================
# get_csrf_token_for_template
# ==========================================================================

class TestGetCsrfTokenForTemplate:

    def test_returns_existing_valid_token(self):
        token = generate_csrf_token()
        signed = sign_csrf_token(token)
        request = MagicMock()
        request.cookies = {CSRF_COOKIE_NAME: signed}
        result = get_csrf_token_for_template(request)
        assert result == signed

    def test_generates_new_if_missing(self):
        request = MagicMock()
        request.cookies = {}
        result = get_csrf_token_for_template(request)
        assert '.' in result
        assert verify_csrf_token(result) is True

    def test_generates_new_if_invalid(self):
        request = MagicMock()
        request.cookies = {CSRF_COOKIE_NAME: "invalid.token"}
        result = get_csrf_token_for_template(request)
        assert verify_csrf_token(result) is True
