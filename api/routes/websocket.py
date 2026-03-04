"""
WebSocket API Routes for Real-Time Monitoring

Provides WebSocket endpoints for parents to receive real-time safety notifications.

Endpoint:
    ws://localhost:8000/api/ws/monitor

Authentication:
    First-message auth pattern. After the WebSocket connection is established,
    the client MUST send an auth message as the very first message:
        {"type": "auth", "token": "<session_token>"}

    The server will close the connection if:
    - No auth message is received within 5 seconds
    - The first message is not a valid auth message
    - The token is invalid or expired
    - The user does not have parent/admin role

Message Types (Server -> Client):
    - connection_established: Initial connection confirmation (after successful auth)
    - safety_incident: Real-time safety incident
    - safety_alert: Critical safety alert requiring attention
    - profile_activity: Child profile activity update
    - pong: Heartbeat response
    - error: Authentication or protocol error

Message Types (Client -> Server):
    - auth: Authentication message (must be first message)
    - ping: Heartbeat to keep connection alive
    - subscribe_profile: Subscribe to specific profile updates
    - get_status: Request connection status

Example Client Code:
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/api/ws/monitor');
    ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'auth', token: sessionToken }));
    };
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'connection_established') {
            // Authenticated successfully
        } else if (data.type === 'safety_incident') {
            showIncidentNotification(data.data);
        } else if (data.type === 'safety_alert') {
            showCriticalAlert(data.data);
        }
    };

    // Keep connection alive
    setInterval(() => {
        ws.send(JSON.stringify({ type: 'ping' }));
    }, 30000);
    ```
"""

import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends

from api.websocket_server import (
    websocket_manager,
    authenticate_websocket,
    handle_websocket_message
)
from core.authentication import AuthSession
from api.middleware.auth import get_current_session
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/monitor")
async def websocket_monitor_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time safety monitoring

    Authentication:
        First-message auth pattern. After connection is accepted, the client
        must send {"type": "auth", "token": "<session_token>"} as the first
        message within 5 seconds. Tokens are NOT passed via query parameters
        to avoid leaking credentials in proxy logs and browser history.

    Connection Flow:
        1. Client connects (no token in URL)
        2. Server accepts the WebSocket connection
        3. Client sends auth message: {"type": "auth", "token": "..."}
        4. Server validates authentication and role
        5. Server sends connection_established confirmation
        6. Client receives real-time safety updates
        7. Client sends periodic ping messages
        8. Server responds with pong
        9. Server broadcasts incidents/alerts as they occur

    SECURED: Only authenticated parents/admins can use this endpoint
    """
    connection_id = None

    # Accept connection first — auth token sent in first message, not URL
    await websocket.accept()

    try:
        # Wait for auth message with short timeout
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            auth_msg = json.loads(raw)
        except asyncio.TimeoutError:
            logger.warning("WebSocket auth timeout — no auth message within 5s")
            await websocket.close(code=1008, reason="Authentication timeout")
            return
        except json.JSONDecodeError:
            logger.warning("WebSocket auth failed — invalid JSON in first message")
            await websocket.close(code=1008, reason="Invalid auth message format")
            return
        except WebSocketDisconnect:
            return

        if auth_msg.get("type") != "auth" or not auth_msg.get("token"):
            await websocket.send_json({
                "type": "error",
                "message": "First message must be {\"type\": \"auth\", \"token\": \"...\"}"
            })
            await websocket.close(code=1008, reason="Invalid auth message")
            return

        # Authenticate
        session = await authenticate_websocket(auth_msg["token"])

        if not session:
            await websocket.send_json({
                "type": "error",
                "message": "Invalid or expired session token"
            })
            await websocket.close(code=1008, reason="Authentication failed")
            return

        # Verify parent/admin role
        if session.role not in ['parent', 'admin']:
            logger.warning(f"WebSocket connection rejected: user {session.user_id} is not parent/admin")
            await websocket.send_json({
                "type": "error",
                "message": "Insufficient permissions"
            })
            await websocket.close(code=1003, reason="Insufficient permissions - parent or admin role required")
            return

        parent_id = session.user_id

        # Register connection (websocket already accepted above)
        connection_id = await websocket_manager.connect(websocket, parent_id)

        logger.info(
            f"Parent {parent_id} connected to real-time monitoring "
            f"(connection_id={connection_id})"
        )

        # Main message loop (with timeout for idle connections)
        WEBSOCKET_TIMEOUT = 300  # 5 minutes idle timeout
        while True:
            try:
                # Wait for message from client with timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=WEBSOCKET_TIMEOUT
                )

                # Parse JSON
                try:
                    message_data = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received from {parent_id}: {data[:100]}")
                    continue

                # Handle message
                await handle_websocket_message(websocket, message_data)

            except asyncio.TimeoutError:
                logger.info(f"WebSocket timeout for {parent_id} after {WEBSOCKET_TIMEOUT}s idle (connection_id={connection_id})")
                await websocket.close(code=1000, reason="Idle timeout")
                break

            except WebSocketDisconnect:
                logger.info(f"Parent {parent_id} disconnected (connection_id={connection_id})")
                break

            except (ConnectionError, RuntimeError) as e:
                logger.error(f"Connection error in WebSocket message loop for {parent_id}: {e}")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket message loop for {parent_id}: {e}")
                # Don't break on error, keep connection alive
                await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected during setup (connection_id={connection_id})")

    except (ConnectionError, RuntimeError) as e:
        logger.error(f"Connection error in WebSocket endpoint: {e}")
        try:
            await websocket.close(code=1011, reason="Connection error")
        except Exception:
            pass  # Connection already closed, ignore
    except Exception as e:
        logger.exception(f"Unexpected error in WebSocket endpoint: {e}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass  # Connection already closed, ignore

    finally:
        # Clean up connection
        if connection_id:
            await websocket_manager.disconnect(websocket)


@router.get("/stats")
async def get_websocket_stats(session: AuthSession = Depends(get_current_session)):
    """
    Get WebSocket connection statistics

    Returns:
        Statistics about active WebSocket connections

    🔒 SECURED: Admin only
    """
    if session.role != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    total_connections = websocket_manager.get_active_connections()

    # Get per-parent connection counts
    parent_stats = {}
    for parent_id, connections in websocket_manager.parent_connections.items():
        parent_stats[parent_id] = len(connections)

    return {
        "total_connections": total_connections,
        "unique_parents": len(websocket_manager.parent_connections),
        "parent_connections": parent_stats,
        "timestamp": asyncio.get_running_loop().time()
    }
