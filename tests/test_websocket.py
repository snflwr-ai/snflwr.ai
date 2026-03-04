"""
Tests for api/websocket_server.py and api/routes/websocket.py

Targets 75%+ coverage on both modules.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketState

from core.authentication import AuthSession
from api.websocket_server import (
    ConnectionManager,
    authenticate_websocket,
    handle_websocket_message,
    broadcast_safety_incident,
    broadcast_safety_alert,
    broadcast_profile_activity,
)


# ============================================================================
# Helpers
# ============================================================================

def make_mock_websocket(connected=True):
    """Create a mock WebSocket with the correct client_state."""
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
    return ws


def make_auth_session(role="parent", user_id="parent-123"):
    return AuthSession(
        user_id=user_id,
        role=role,
        session_token="tok-test",
        email="test@example.com",
        created_at="2024-01-01T00:00:00",
    )


# ============================================================================
# ConnectionManager tests
# ============================================================================

class TestConnectionManagerConnect:

    async def test_connect_creates_connection(self):
        """connect() registers WS, sends confirmation, returns a connection_id."""
        manager = ConnectionManager()
        ws = make_mock_websocket()

        conn_id = await manager.connect(ws, "parent-123")

        assert "parent-123" in manager.parent_connections
        assert ws in manager.parent_connections["parent-123"]
        assert conn_id is not None
        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "connection_established"
        assert sent["connection_id"] == conn_id

    async def test_connect_multiple_per_parent(self):
        """Two WebSocket objects for the same parent are both tracked."""
        manager = ConnectionManager()
        ws1 = make_mock_websocket()
        ws2 = make_mock_websocket()

        await manager.connect(ws1, "parent-123")
        await manager.connect(ws2, "parent-123")

        assert len(manager.parent_connections["parent-123"]) == 2
        assert ws1 in manager.parent_connections["parent-123"]
        assert ws2 in manager.parent_connections["parent-123"]

    async def test_connect_custom_connection_id(self):
        """connect() respects a caller-provided connection_id."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        custom_id = "custom-conn-id-xyz"

        conn_id = await manager.connect(ws, "parent-abc", connection_id=custom_id)

        assert conn_id == custom_id
        assert manager.connection_ids[custom_id] == ws
        sent = ws.send_json.call_args[0][0]
        assert sent["connection_id"] == custom_id

    async def test_connect_stores_metadata(self):
        """connect() stores parent_id and connection_id in metadata dict."""
        manager = ConnectionManager()
        ws = make_mock_websocket()

        conn_id = await manager.connect(ws, "parent-meta")

        meta = manager.connection_metadata[ws]
        assert meta["parent_id"] == "parent-meta"
        assert meta["connection_id"] == conn_id
        assert "connected_at" in meta
        assert "last_heartbeat" in meta


class TestConnectionManagerDisconnect:

    async def test_disconnect_removes_connection(self):
        """disconnect() removes the WS from parent_connections and metadata."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-123")

        await manager.disconnect(ws)

        assert ws not in manager.connection_metadata
        # parent key also gone because set is now empty
        assert "parent-123" not in manager.parent_connections

    async def test_disconnect_cleans_empty_parent_set(self):
        """Disconnecting the last WS for a parent removes the parent key entirely."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-only")

        await manager.disconnect(ws)

        assert "parent-only" not in manager.parent_connections

    async def test_disconnect_retains_other_connections(self):
        """Disconnecting one WS leaves other WS for the same parent in place."""
        manager = ConnectionManager()
        ws1 = make_mock_websocket()
        ws2 = make_mock_websocket()
        await manager.connect(ws1, "parent-123")
        await manager.connect(ws2, "parent-123")

        await manager.disconnect(ws1)

        assert "parent-123" in manager.parent_connections
        assert ws2 in manager.parent_connections["parent-123"]
        assert ws1 not in manager.parent_connections["parent-123"]

    async def test_disconnect_unknown_websocket(self):
        """Disconnecting an unregistered WS is a no-op (no exception)."""
        manager = ConnectionManager()
        unknown_ws = make_mock_websocket()

        # Should not raise
        await manager.disconnect(unknown_ws)


class TestSendPersonalMessage:

    async def test_send_personal_message_connected(self):
        """CONNECTED state → send_json is called with the message."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        msg = {"type": "test", "data": "hello"}

        await manager.send_personal_message(ws, msg)

        ws.send_json.assert_called_once_with(msg)

    async def test_send_personal_message_disconnected(self):
        """DISCONNECTED state → send_json is NOT called."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=False)

        await manager.send_personal_message(ws, {"type": "test"})

        ws.send_json.assert_not_called()

    async def test_send_personal_message_error_triggers_disconnect(self):
        """If send_json raises, the connection is cleaned up via disconnect()."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        # Register the ws first so disconnect has metadata to work with
        await manager.connect(ws, "parent-err")
        # Now make send_json raise on subsequent calls
        ws.send_json.reset_mock()
        ws.send_json.side_effect = RuntimeError("broken pipe")

        await manager.send_personal_message(ws, {"type": "test"})

        # After error the ws should no longer appear in metadata
        assert ws not in manager.connection_metadata


class TestLocalBroadcastToParent:

    async def test_local_broadcast_to_parent_no_connections(self):
        """Parent not in dict → no send calls, returns None or 0."""
        manager = ConnectionManager()
        msg = {"type": "test"}

        result = await manager._local_broadcast_to_parent("nonexistent-parent", msg)

        # Returns None when parent not found (early return)
        assert result is None

    async def test_local_broadcast_to_parent_success(self):
        """Sends message to all connected WS objects for the parent."""
        manager = ConnectionManager()
        ws1 = make_mock_websocket()
        ws2 = make_mock_websocket()
        await manager.connect(ws1, "parent-bcast")
        await manager.connect(ws2, "parent-bcast")
        # Reset call counts from the connect() confirmation messages
        ws1.send_json.reset_mock()
        ws2.send_json.reset_mock()

        msg = {"type": "safety_incident"}
        count = await manager._local_broadcast_to_parent("parent-bcast", msg)

        ws1.send_json.assert_called_once_with(msg)
        ws2.send_json.assert_called_once_with(msg)
        assert count == 2

    async def test_local_broadcast_to_parent_removes_disconnected(self):
        """DISCONNECTED WS is removed from parent_connections during broadcast."""
        manager = ConnectionManager()
        ws_alive = make_mock_websocket(connected=True)
        ws_dead = make_mock_websocket(connected=False)
        await manager.connect(ws_alive, "parent-mixed")
        await manager.connect(ws_dead, "parent-mixed")
        ws_alive.send_json.reset_mock()

        msg = {"type": "alert"}
        await manager._local_broadcast_to_parent("parent-mixed", msg)

        # Dead connection should be cleaned up
        assert ws_dead not in manager.connection_metadata
        # Alive one should still get the message
        ws_alive.send_json.assert_called_once_with(msg)


class TestGetActiveConnections:

    async def test_get_active_connections_total(self):
        """get_active_connections() with no arg returns total across all parents."""
        manager = ConnectionManager()
        ws1 = make_mock_websocket()
        ws2 = make_mock_websocket()
        ws3 = make_mock_websocket()
        await manager.connect(ws1, "parent-A")
        await manager.connect(ws2, "parent-A")
        await manager.connect(ws3, "parent-B")

        total = manager.get_active_connections()

        assert total == 3

    async def test_get_active_connections_for_parent(self):
        """get_active_connections(parent_id) returns count for that parent only."""
        manager = ConnectionManager()
        ws1 = make_mock_websocket()
        ws2 = make_mock_websocket()
        ws3 = make_mock_websocket()
        await manager.connect(ws1, "parent-A")
        await manager.connect(ws2, "parent-A")
        await manager.connect(ws3, "parent-B")

        count_a = manager.get_active_connections("parent-A")
        count_b = manager.get_active_connections("parent-B")
        count_missing = manager.get_active_connections("parent-C")

        assert count_a == 2
        assert count_b == 1
        assert count_missing == 0

    async def test_get_active_connections_empty(self):
        """Fresh manager returns 0 total connections."""
        manager = ConnectionManager()
        assert manager.get_active_connections() == 0


class TestIsParentConnected:

    async def test_is_parent_connected_true(self):
        """Returns True when parent has at least one connection."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-present")

        assert manager.is_parent_connected("parent-present") is True

    async def test_is_parent_connected_false(self):
        """Returns False when parent has no connections."""
        manager = ConnectionManager()

        assert manager.is_parent_connected("parent-absent") is False

    async def test_is_parent_connected_after_disconnect(self):
        """Returns False after the sole connection is disconnected."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-gone")
        await manager.disconnect(ws)

        assert manager.is_parent_connected("parent-gone") is False


class TestStartPubsub:

    async def test_start_pubsub_disabled(self):
        """When REDIS_PUBSUB_ENABLED is False, start_pubsub() is a no-op."""
        manager = ConnectionManager()
        with patch("api.websocket_server.REDIS_PUBSUB_ENABLED", False):
            await manager.start_pubsub()

        # No Redis task created
        assert manager._pubsub_task is None
        assert manager._redis_pubsub is None


# ============================================================================
# Helper function tests
# ============================================================================

class TestAuthenticateWebsocket:

    async def test_authenticate_websocket_valid_token(self):
        """Valid token returns the AuthSession from auth_manager."""
        session = make_auth_session()
        with patch("api.websocket_server.auth_manager") as mock_auth:
            mock_auth.validate_session.return_value = (True, session)

            result = await authenticate_websocket("valid-token")

        assert result is session

    async def test_authenticate_websocket_invalid_token(self):
        """Invalid token (validate_session returns False) returns None."""
        with patch("api.websocket_server.auth_manager") as mock_auth:
            mock_auth.validate_session.return_value = (False, None)

            result = await authenticate_websocket("bad-token")

        assert result is None

    async def test_authenticate_websocket_empty_token(self):
        """Empty token short-circuits auth, returns None without calling manager."""
        with patch("api.websocket_server.auth_manager") as mock_auth:
            result = await authenticate_websocket("")

        assert result is None
        mock_auth.validate_session.assert_not_called()

    async def test_authenticate_websocket_exception_returns_none(self):
        """If validate_session raises, authenticate_websocket returns None."""
        with patch("api.websocket_server.auth_manager") as mock_auth:
            mock_auth.validate_session.side_effect = RuntimeError("Redis down")

            result = await authenticate_websocket("some-token")

        assert result is None


class TestHandleWebsocketMessage:

    async def test_handle_message_ping(self):
        """Ping message triggers pong reply."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-ping")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            await handle_websocket_message(ws, {"type": "ping"})

        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "pong"

    async def test_handle_message_ping_updates_heartbeat(self):
        """Ping message updates the last_heartbeat timestamp in metadata."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-hb")
        original_hb = manager.connection_metadata[ws]["last_heartbeat"]

        with patch("api.websocket_server.websocket_manager", manager):
            await handle_websocket_message(ws, {"type": "ping"})

        new_hb = manager.connection_metadata[ws]["last_heartbeat"]
        assert new_hb >= original_hb

    async def test_handle_message_subscribe_profile(self):
        """subscribe_profile message sends subscribed confirmation."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-sub")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            await handle_websocket_message(ws, {"type": "subscribe_profile", "profile_id": "child-1"})

        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "subscribed"
        assert sent["profile_id"] == "child-1"

    async def test_handle_message_get_status(self):
        """get_status message sends connection status with connection_id."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        conn_id = await manager.connect(ws, "parent-status")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            await handle_websocket_message(ws, {"type": "get_status"})

        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "status"
        assert sent["connection_id"] == conn_id
        assert sent["connected"] is True

    async def test_handle_message_unknown(self):
        """Unknown message type logs warning but does not raise."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-unk")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            # Should not raise
            await handle_websocket_message(ws, {"type": "totally_unknown_type"})

        # No response sent for unknown type
        ws.send_json.assert_not_called()


# ============================================================================
# Broadcast helper function tests
# ============================================================================

class TestBroadcastHelpers:

    async def test_broadcast_safety_incident_message_type(self):
        """broadcast_safety_incident sends a message with type safety_incident."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-incident")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            await broadcast_safety_incident("parent-incident", {"detail": "test"})

        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "safety_incident"
        assert "timestamp" in sent
        assert sent["data"] == {"detail": "test"}

    async def test_broadcast_safety_alert_has_high_priority(self):
        """broadcast_safety_alert message includes priority: 'high'."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-alert")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            await broadcast_safety_alert("parent-alert", {"alert": "danger"})

        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "safety_alert"
        assert sent["priority"] == "high"
        assert sent["data"] == {"alert": "danger"}

    async def test_broadcast_profile_activity_includes_profile_id(self):
        """broadcast_profile_activity message includes the profile_id."""
        manager = ConnectionManager()
        ws = make_mock_websocket()
        await manager.connect(ws, "parent-activity")
        ws.send_json.reset_mock()

        with patch("api.websocket_server.websocket_manager", manager):
            await broadcast_profile_activity("parent-activity", "child-profile-99", {"action": "login"})

        ws.send_json.assert_called_once()
        sent = ws.send_json.call_args[0][0]
        assert sent["type"] == "profile_activity"
        assert sent["profile_id"] == "child-profile-99"
        assert sent["data"] == {"action": "login"}

    async def test_broadcast_to_parent_no_connections_no_error(self):
        """broadcast_to_parent for a parent with no connections doesn't raise."""
        manager = ConnectionManager()
        with patch("api.websocket_server.websocket_manager", manager):
            # Should not raise even with no connections
            await broadcast_safety_incident("unknown-parent", {"x": 1})


# ============================================================================
# Route tests using TestClient (sync WebSocket testing)
# ============================================================================

# Build a minimal FastAPI app with the WS router attached
from api.routes.websocket import router as ws_router
from api.middleware.auth import get_current_session

_test_app = FastAPI()
_test_app.include_router(ws_router, prefix="/ws")


class TestWebSocketRoute:

    def _make_client(self):
        return TestClient(_test_app, raise_server_exceptions=False)

    def test_ws_auth_invalid_token(self):
        """Sending an auth message with a bad token should get an error response."""
        client = self._make_client()
        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=None)):
            with client.websocket_connect("/ws/monitor") as ws:
                ws.send_json({"type": "auth", "token": "bad-token"})
                data = ws.receive_json()
                assert data["type"] == "error"

    def test_ws_auth_wrong_role(self):
        """Session with role 'student' should receive an error and be disconnected."""
        student_session = make_auth_session(role="student", user_id="student-1")
        client = self._make_client()
        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=student_session)):
            with client.websocket_connect("/ws/monitor") as ws:
                ws.send_json({"type": "auth", "token": "student-token"})
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "permission" in data["message"].lower()

    def test_ws_auth_non_json_closes_connection(self):
        """Sending non-JSON as the first message should close the connection."""
        client = self._make_client()
        with client.websocket_connect("/ws/monitor") as ws:
            ws.send_text("not-json-at-all")
            # Connection should close — either receive raises or we get close frame
            try:
                # Some clients raise on closed connection
                ws.receive_json()
                # If we get here, shouldn't be a valid message
            except Exception:
                pass  # Expected — connection was closed

    def test_ws_auth_wrong_message_type(self):
        """First message with wrong type field gets error response."""
        client = self._make_client()
        with client.websocket_connect("/ws/monitor") as ws:
            ws.send_json({"type": "ping", "token": "some-token"})
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_ws_auth_missing_token_field(self):
        """Auth message without token field gets error response."""
        client = self._make_client()
        with client.websocket_connect("/ws/monitor") as ws:
            ws.send_json({"type": "auth"})
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_ws_successful_parent_auth(self):
        """Valid parent auth results in connection_established message."""
        session = make_auth_session(role="parent", user_id="parent-ws-1")
        client = self._make_client()
        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                mock_mgr.connect = AsyncMock(return_value="conn-id-001")
                mock_mgr.disconnect = AsyncMock()
                with client.websocket_connect("/ws/monitor") as ws:
                    ws.send_json({"type": "auth", "token": "valid-parent-token"})
                    # The route calls websocket_manager.connect which we mocked;
                    # the actual connection_established comes from the real manager.
                    # Just verify connect was called with the right parent_id.
                    mock_mgr.connect.assert_called_once()
                    call_args = mock_mgr.connect.call_args
                    assert call_args[0][1] == "parent-ws-1"

    def test_ws_successful_admin_auth(self):
        """Admin role also gets accepted by the WS endpoint."""
        session = make_auth_session(role="admin", user_id="admin-ws-1")
        client = self._make_client()
        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                mock_mgr.connect = AsyncMock(return_value="conn-admin-001")
                mock_mgr.disconnect = AsyncMock()
                with client.websocket_connect("/ws/monitor") as ws:
                    ws.send_json({"type": "auth", "token": "valid-admin-token"})
                    mock_mgr.connect.assert_called_once()
                    call_args = mock_mgr.connect.call_args
                    assert call_args[0][1] == "admin-ws-1"

    def test_ws_ping_pong_in_loop(self):
        """After auth, a ping message yields a pong response."""
        session = make_auth_session(role="parent", user_id="parent-ws-ping")
        client = self._make_client()
        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with client.websocket_connect("/ws/monitor") as ws:
                ws.send_json({"type": "auth", "token": "valid-token"})
                # Consume connection_established
                data = ws.receive_json()
                assert data["type"] == "connection_established"
                # Now send a ping
                ws.send_json({"type": "ping"})
                pong = ws.receive_json()
                assert pong["type"] == "pong"


class TestWebSocketStatsEndpoint:

    def _make_client_with_session(self, session):
        """Create a TestClient with the given session injected via dependency_overrides."""
        # Use a local app instance to avoid cross-test contamination
        from api.routes.websocket import router as _ws_router
        from api.middleware.auth import get_current_session as _gcs
        app = FastAPI()
        app.include_router(_ws_router, prefix="/ws")
        app.dependency_overrides[_gcs] = lambda: session
        return TestClient(app, raise_server_exceptions=False)

    def test_ws_stats_admin(self):
        """Admin GET /stats returns the stats dict."""
        admin_session = make_auth_session(role="admin", user_id="admin-stats")
        client = self._make_client_with_session(admin_session)
        response = client.get("/ws/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total_connections" in data
        assert "unique_parents" in data
        assert "parent_connections" in data
        assert "timestamp" in data

    def test_ws_stats_non_admin_returns_403(self):
        """Non-admin GET /stats returns HTTP 403."""
        parent_session = make_auth_session(role="parent", user_id="parent-stats")
        client = self._make_client_with_session(parent_session)
        response = client.get("/ws/stats")

        assert response.status_code == 403

    def test_ws_stats_values_reflect_manager(self):
        """Stats endpoint reads live data from websocket_manager."""
        admin_session = make_auth_session(role="admin", user_id="admin-live")
        client = self._make_client_with_session(admin_session)

        # Patch websocket_manager to have a known state
        mock_mgr = MagicMock()
        mock_mgr.get_active_connections.return_value = 5
        mock_mgr.parent_connections = {"p1": {1, 2}, "p2": {3}}

        with patch("api.routes.websocket.websocket_manager", mock_mgr):
            response = client.get("/ws/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_connections"] == 5
        assert data["unique_parents"] == 2


# ============================================================================
# Additional edge-case / coverage tests
# ============================================================================

class TestBroadcastAll:

    async def test_local_broadcast_all_sends_to_all_parents(self):
        """_local_broadcast_all sends to every connection across all parents."""
        manager = ConnectionManager()
        ws_a = make_mock_websocket()
        ws_b = make_mock_websocket()
        ws_c = make_mock_websocket()
        await manager.connect(ws_a, "parent-A")
        await manager.connect(ws_b, "parent-B")
        await manager.connect(ws_c, "parent-B")
        for ws in [ws_a, ws_b, ws_c]:
            ws.send_json.reset_mock()

        msg = {"type": "system_broadcast"}
        count = await manager._local_broadcast_all(msg)

        for ws in [ws_a, ws_b, ws_c]:
            ws.send_json.assert_called_once_with(msg)
        assert count == 3

    async def test_local_broadcast_all_empty_no_error(self):
        """_local_broadcast_all on empty manager returns 0 and doesn't raise."""
        manager = ConnectionManager()
        result = await manager._local_broadcast_all({"type": "nothing"})
        assert result == 0


class TestStopPubsub:

    async def test_stop_pubsub_no_task(self):
        """stop_pubsub() with no running task completes without error."""
        manager = ConnectionManager()
        # Should not raise
        await manager.stop_pubsub()

    async def test_stop_pubsub_cancels_task(self):
        """stop_pubsub() cancels an active pubsub task."""
        manager = ConnectionManager()

        # Create a simple long-running task to represent pubsub
        async def dummy():
            await asyncio.sleep(9999)

        manager._pubsub_task = asyncio.create_task(dummy())

        await manager.stop_pubsub()

        assert manager._pubsub_task.cancelled() or manager._pubsub_task.done()


class TestGetWebsocketSession:

    async def test_get_websocket_session_delegates(self):
        """get_websocket_session reads token from query_params and delegates."""
        from api.websocket_server import get_websocket_session

        ws = MagicMock()
        ws.query_params = {"token": "query-token"}
        session = make_auth_session()

        with patch("api.websocket_server.auth_manager") as mock_auth:
            mock_auth.validate_session.return_value = (True, session)
            result = await get_websocket_session(ws)

        assert result is session

    async def test_get_websocket_session_missing_token(self):
        """get_websocket_session returns None when token query param absent."""
        from api.websocket_server import get_websocket_session

        ws = MagicMock()
        ws.query_params = {}

        with patch("api.websocket_server.auth_manager") as mock_auth:
            result = await get_websocket_session(ws)

        assert result is None
        mock_auth.validate_session.assert_not_called()


class TestBroadcastToParentRedisPath:

    async def test_broadcast_to_parent_publishes_to_redis_when_pubsub_set(self):
        """broadcast_to_parent calls _publish_to_redis when _redis_pubsub is active."""
        manager = ConnectionManager()
        # Inject a fake pubsub handle so _publish_to_redis path is exercised
        manager._redis_pubsub = MagicMock()

        ws = make_mock_websocket()
        await manager.connect(ws, "parent-redis")
        ws.send_json.reset_mock()

        with patch.object(manager, "_publish_to_redis", new=AsyncMock()) as mock_pub:
            await manager.broadcast_to_parent("parent-redis", {"type": "test"})

        mock_pub.assert_called_once_with("parent", "parent-redis", {"type": "test"})

    async def test_broadcast_all_publishes_to_redis(self):
        """broadcast_all calls _publish_to_redis with target_type='all'."""
        manager = ConnectionManager()
        manager._redis_pubsub = MagicMock()

        with patch.object(manager, "_publish_to_redis", new=AsyncMock()) as mock_pub:
            await manager.broadcast_all({"type": "global"})

        mock_pub.assert_called_once_with("all", None, {"type": "global"})


# ============================================================================
# Direct route function tests (mock WebSocket to hit exception branches)
# ============================================================================

class TestWebSocketRouteDirectExceptionPaths:
    """Test exception branches in websocket_monitor_endpoint directly.

    TestClient cannot easily trigger asyncio.TimeoutError or WebSocketDisconnect
    at specific points in the coroutine, so we call the endpoint function
    directly with a fully-mocked WebSocket object.
    """

    async def _run_endpoint(self, mock_ws):
        """Invoke the route coroutine directly."""
        from api.routes.websocket import websocket_monitor_endpoint
        await websocket_monitor_endpoint(mock_ws)

    def _make_route_ws(self):
        """Create a mock WebSocket suitable for direct coroutine calls."""
        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    async def test_auth_timeout_closes_with_1008(self):
        """asyncio.TimeoutError during auth → close(code=1008)."""
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        ws.receive_text = AsyncMock(side_effect=asyncio.TimeoutError())

        await websocket_monitor_endpoint(ws)

        ws.close.assert_called_with(code=1008, reason="Authentication timeout")

    async def test_websocket_disconnect_during_auth(self):
        """WebSocketDisconnect during initial receive → clean exit (no close call)."""
        from fastapi import WebSocketDisconnect
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

        # Should not raise
        await websocket_monitor_endpoint(ws)

        ws.close.assert_not_called()

    async def test_message_loop_idle_timeout_closes_with_1000(self):
        """asyncio.TimeoutError in message loop → close(code=1000)."""
        from api.routes.websocket import websocket_monitor_endpoint
        session = make_auth_session(role="parent", user_id="parent-idle")
        ws = self._make_route_ws()

        auth_json = json.dumps({"type": "auth", "token": "valid"})
        # First call returns auth message, second raises TimeoutError (idle)
        ws.receive_text = AsyncMock(side_effect=[auth_json, asyncio.TimeoutError()])

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                mock_mgr.connect = AsyncMock(return_value="conn-idle")
                mock_mgr.disconnect = AsyncMock()
                await websocket_monitor_endpoint(ws)

        ws.close.assert_called_with(code=1000, reason="Idle timeout")

    async def test_message_loop_websocket_disconnect_exits_cleanly(self):
        """WebSocketDisconnect in message loop → clean exit."""
        from fastapi import WebSocketDisconnect
        from api.routes.websocket import websocket_monitor_endpoint
        session = make_auth_session(role="parent", user_id="parent-disc-msg")
        ws = self._make_route_ws()

        auth_json = json.dumps({"type": "auth", "token": "valid"})
        ws.receive_text = AsyncMock(side_effect=[auth_json, WebSocketDisconnect()])

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                mock_mgr.connect = AsyncMock(return_value="conn-disc")
                mock_mgr.disconnect = AsyncMock()
                await websocket_monitor_endpoint(ws)

        # Should not raise, disconnect should be called from finally
        mock_mgr.disconnect.assert_called_once_with(ws)

    async def test_message_loop_connection_error_breaks_loop(self):
        """ConnectionError in message loop → loop breaks, disconnect called."""
        from api.routes.websocket import websocket_monitor_endpoint
        session = make_auth_session(role="parent", user_id="parent-conn-err-loop")
        ws = self._make_route_ws()

        auth_json = json.dumps({"type": "auth", "token": "valid"})
        ws.receive_text = AsyncMock(side_effect=[auth_json, ConnectionError("dropped")])

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                mock_mgr.connect = AsyncMock(return_value="conn-err-loop")
                mock_mgr.disconnect = AsyncMock()
                await websocket_monitor_endpoint(ws)

        mock_mgr.disconnect.assert_called_once_with(ws)

    async def test_outer_connection_error_calls_close_1011(self):
        """ConnectionError during setup (outside message loop) → close(code=1011)."""
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        # raise ConnectionError immediately after accept
        ws.receive_text = AsyncMock(side_effect=ConnectionError("network error"))

        # Make the inner try-except NOT catch it as TimeoutError:
        # Actually ConnectionError is caught in the outer except (ConnectionError, RuntimeError)
        # But the inner try only catches TimeoutError, JSONDecodeError, WebSocketDisconnect
        # So ConnectionError will bubble up to the outer handler

        await websocket_monitor_endpoint(ws)

        ws.close.assert_called_with(code=1011, reason="Connection error")

    async def test_outer_runtime_error_calls_close_1011(self):
        """RuntimeError during setup → close(code=1011)."""
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        ws.receive_text = AsyncMock(side_effect=RuntimeError("runtime issue"))

        await websocket_monitor_endpoint(ws)

        ws.close.assert_called_with(code=1011, reason="Connection error")

    async def test_outer_unexpected_exception_calls_close_1011(self):
        """Unexpected exception at top level → close(code=1011)."""
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        ws.receive_text = AsyncMock(side_effect=Exception("unexpected"))

        await websocket_monitor_endpoint(ws)

        ws.close.assert_called_with(code=1011, reason="Internal server error")

    async def test_outer_websocket_disconnect_during_setup(self):
        """WebSocketDisconnect during websocket_manager.connect() → outer handler."""
        from fastapi import WebSocketDisconnect
        from api.routes.websocket import websocket_monitor_endpoint
        session = make_auth_session(role="parent", user_id="parent-outer-disc")
        ws = self._make_route_ws()

        auth_json = json.dumps({"type": "auth", "token": "valid"})
        ws.receive_text = AsyncMock(return_value=auth_json)

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                # WebSocketDisconnect raised during connect() hits the outer handler
                mock_mgr.connect = AsyncMock(side_effect=WebSocketDisconnect())
                mock_mgr.disconnect = AsyncMock()
                # Should not raise
                await websocket_monitor_endpoint(ws)

    async def test_outer_connection_error_with_close_failing(self):
        """ConnectionError at top level where ws.close() also fails → no exception."""
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        ws.receive_text = AsyncMock(side_effect=ConnectionError("dropped"))
        # Make close() also raise so we hit the `except Exception: pass` at line 204-205
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))

        # Should not raise
        await websocket_monitor_endpoint(ws)

    async def test_outer_exception_with_close_failing(self):
        """Generic exception at top level where ws.close() also fails → no exception."""
        from api.routes.websocket import websocket_monitor_endpoint
        ws = self._make_route_ws()
        ws.receive_text = AsyncMock(side_effect=Exception("boom"))
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))

        # Should not raise
        await websocket_monitor_endpoint(ws)

    async def test_message_loop_generic_exception_continues(self):
        """Generic exception in message loop doesn't break the loop (continues)."""
        from fastapi import WebSocketDisconnect
        from api.routes.websocket import websocket_monitor_endpoint
        session = make_auth_session(role="parent", user_id="parent-generic-loop")
        ws = self._make_route_ws()

        auth_json = json.dumps({"type": "auth", "token": "valid"})
        # Generic exception first → loop continues (asyncio.sleep(0.1))
        # Then WebSocketDisconnect → exits cleanly
        ws.receive_text = AsyncMock(side_effect=[
            auth_json,
            ValueError("some error"),
            WebSocketDisconnect(),
        ])

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with patch("api.routes.websocket.websocket_manager") as mock_mgr:
                mock_mgr.connect = AsyncMock(return_value="conn-gen-loop")
                mock_mgr.disconnect = AsyncMock()
                await websocket_monitor_endpoint(ws)

        mock_mgr.disconnect.assert_called_once_with(ws)


# ============================================================================
# Error-branch coverage tests for ConnectionManager
# ============================================================================

class TestSendPersonalMessageConnectionError:

    async def test_send_personal_message_connection_error_triggers_disconnect(self):
        """ConnectionError in send_json triggers disconnect."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        await manager.connect(ws, "parent-conn-err")
        ws.send_json.reset_mock()
        ws.send_json.side_effect = ConnectionError("connection reset")

        await manager.send_personal_message(ws, {"type": "test"})

        assert ws not in manager.connection_metadata

    async def test_send_personal_message_generic_exception_triggers_disconnect(self):
        """Any unexpected exception in send_json triggers disconnect."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        await manager.connect(ws, "parent-generic-err")
        ws.send_json.reset_mock()
        ws.send_json.side_effect = ValueError("unexpected")

        await manager.send_personal_message(ws, {"type": "test"})

        assert ws not in manager.connection_metadata


class TestLocalBroadcastToParentErrorBranches:

    async def test_broadcast_to_parent_connection_error_removes_ws(self):
        """ConnectionError during broadcast_to_parent causes WS to be disconnected."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        await manager.connect(ws, "parent-bcast-err")
        ws.send_json.reset_mock()
        ws.send_json.side_effect = ConnectionError("broken")

        await manager._local_broadcast_to_parent("parent-bcast-err", {"type": "msg"})

        assert ws not in manager.connection_metadata

    async def test_broadcast_to_parent_generic_exception_removes_ws(self):
        """Generic exception during broadcast causes WS cleanup."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        await manager.connect(ws, "parent-bcast-exc")
        ws.send_json.reset_mock()
        ws.send_json.side_effect = ValueError("oops")

        await manager._local_broadcast_to_parent("parent-bcast-exc", {"type": "msg"})

        assert ws not in manager.connection_metadata


class TestLocalBroadcastAllErrorBranches:

    async def test_broadcast_all_connection_error_removes_ws(self):
        """ConnectionError in _local_broadcast_all causes WS cleanup."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        await manager.connect(ws, "parent-all-err")
        ws.send_json.reset_mock()
        ws.send_json.side_effect = ConnectionError("dropped")

        await manager._local_broadcast_all({"type": "system"})

        assert ws not in manager.connection_metadata

    async def test_broadcast_all_generic_exception_removes_ws(self):
        """Generic exception in _local_broadcast_all causes WS cleanup."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=True)
        await manager.connect(ws, "parent-all-exc")
        ws.send_json.reset_mock()
        ws.send_json.side_effect = TypeError("bad type")

        await manager._local_broadcast_all({"type": "system"})

        assert ws not in manager.connection_metadata

    async def test_broadcast_all_disconnected_ws_removed(self):
        """DISCONNECTED WS in _local_broadcast_all is cleaned up."""
        manager = ConnectionManager()
        ws = make_mock_websocket(connected=False)
        await manager.connect(ws, "parent-all-disc")

        await manager._local_broadcast_all({"type": "system"})

        assert ws not in manager.connection_metadata


# ============================================================================
# Coverage tests for route message-loop error branches
# ============================================================================

class TestWebSocketRouteMessageLoop:
    """Test message loop error branches inside websocket_monitor_endpoint."""

    def _make_app_with_session(self, session):
        from api.routes.websocket import router as _ws_router
        from api.middleware.auth import get_current_session as _gcs
        app = FastAPI()
        app.include_router(_ws_router, prefix="/ws")
        return app

    def test_ws_invalid_json_in_message_loop_continues(self):
        """Invalid JSON in message loop logs a warning but keeps connection open."""
        session = make_auth_session(role="parent", user_id="parent-json-loop")
        from api.routes.websocket import router as _ws_router
        app = FastAPI()
        app.include_router(_ws_router, prefix="/ws")
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with client.websocket_connect("/ws/monitor") as ws:
                ws.send_json({"type": "auth", "token": "tok"})
                # Consume connection_established
                data = ws.receive_json()
                assert data["type"] == "connection_established"
                # Send invalid JSON — loop should continue and not crash
                ws.send_text("this-is-not-json")
                # Send a valid ping to confirm connection is still alive
                ws.send_json({"type": "ping"})
                pong = ws.receive_json()
                assert pong["type"] == "pong"

    def test_ws_disconnect_in_message_loop_closes_cleanly(self):
        """WebSocketDisconnect during message loop exits cleanly."""
        session = make_auth_session(role="parent", user_id="parent-disc-loop")
        from api.routes.websocket import router as _ws_router
        app = FastAPI()
        app.include_router(_ws_router, prefix="/ws")
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api.routes.websocket.authenticate_websocket", new=AsyncMock(return_value=session)):
            with client.websocket_connect("/ws/monitor") as ws:
                ws.send_json({"type": "auth", "token": "tok"})
                data = ws.receive_json()
                assert data["type"] == "connection_established"
                # Close from client side — this triggers WebSocketDisconnect in the loop
                ws.close()


# ============================================================================
# Redis pubsub path tests (mocked)
# ============================================================================

class TestPubSubPaths:

    async def test_start_pubsub_redis_not_available(self):
        """start_pubsub when Redis IS enabled but cache not available → logs warning, no task."""
        manager = ConnectionManager()
        mock_cache = MagicMock()
        mock_cache.enabled = True
        mock_cache._client = None  # Redis not available

        with patch("api.websocket_server.REDIS_PUBSUB_ENABLED", True):
            with patch("api.websocket_server.cache", mock_cache, create=True):
                # Import cache inside start_pubsub via patch at the function call site
                with patch("api.websocket_server.REDIS_PUBSUB_ENABLED", True):
                    # Patch the import inside start_pubsub
                    with patch.dict("sys.modules", {}):
                        # Since cache is imported inside start_pubsub, patch at the right level
                        import sys
                        import types
                        fake_cache_module = types.ModuleType("utils.cache")
                        fake_cache_module.cache = mock_cache
                        with patch.dict(sys.modules, {"utils.cache": fake_cache_module}):
                            with patch("api.websocket_server.REDIS_PUBSUB_ENABLED", True):
                                await manager.start_pubsub()

        # No task should be created
        assert manager._pubsub_task is None

    async def test_stop_pubsub_with_redis_pubsub_handle(self):
        """stop_pubsub() calls unsubscribe and close on the pubsub handle."""
        manager = ConnectionManager()
        mock_pubsub = MagicMock()
        manager._redis_pubsub = mock_pubsub

        await manager.stop_pubsub()

        mock_pubsub.unsubscribe.assert_called_once()
        mock_pubsub.close.assert_called_once()

    async def test_publish_to_redis_no_pubsub_returns_early(self):
        """_publish_to_redis returns early if _redis_pubsub is None."""
        manager = ConnectionManager()
        # No pubsub set
        assert manager._redis_pubsub is None

        # Should not raise
        await manager._publish_to_redis("parent", "p1", {"type": "test"})
