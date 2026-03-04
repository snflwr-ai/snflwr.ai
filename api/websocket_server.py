"""
WebSocket Server for Real-Time Safety Monitoring

Provides real-time incident streaming to parent dashboards via WebSocket connections.

Features:
- Per-parent WebSocket channels
- Real-time safety incident broadcasting
- Connection management with authentication
- Heartbeat/ping-pong for connection health
- Automatic reconnection support
- Redis Pub/Sub for horizontal scaling across multiple instances

Usage:
    from api.websocket_server import websocket_manager

    # Broadcast incident to parent
    await websocket_manager.broadcast_to_parent(parent_id, incident_data)
"""

import asyncio
import json
import os
from typing import Dict, Set, Optional, Any
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect, Depends, status
from fastapi.websockets import WebSocketState
import uuid

from core.authentication import auth_manager, AuthSession
from utils.logger import get_logger

logger = get_logger(__name__)

# Conditional Redis error import
try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError  # Fallback so except RedisError still works

# Redis Pub/Sub configuration
REDIS_PUBSUB_ENABLED = os.getenv('REDIS_ENABLED', 'false').lower() == 'true'
WEBSOCKET_CHANNEL = 'snflwr:websocket:broadcast'


class ConnectionManager:
    """
    Manages WebSocket connections for real-time safety monitoring

    Architecture:
    - parent_connections: Maps parent_id -> set of WebSocket connections
    - connection_metadata: Stores metadata for each connection
    - Supports multiple connections per parent (multiple browser tabs/devices)
    - Redis Pub/Sub for horizontal scaling across multiple server instances
    """

    def __init__(self):
        # parent_id -> Set[WebSocket]
        self.parent_connections: Dict[str, Set[WebSocket]] = {}

        # WebSocket -> metadata dict
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}

        # Connection ID -> WebSocket (for easier lookup)
        self.connection_ids: Dict[str, WebSocket] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        # Redis Pub/Sub for horizontal scaling
        self._redis_pubsub = None
        self._pubsub_task = None
        self._instance_id = str(uuid.uuid4())[:8]  # Unique ID for this instance

    async def start_pubsub(self):
        """
        Start Redis Pub/Sub listener for cross-instance communication.

        This allows broadcasts from other server instances to be received
        and forwarded to local WebSocket connections.
        """
        if not REDIS_PUBSUB_ENABLED:
            logger.info("Redis Pub/Sub disabled - WebSocket broadcasts are local only")
            return

        try:
            from utils.cache import cache
            if not cache.enabled or not cache._client:
                logger.warning("Redis not available - WebSocket broadcasts are local only")
                return

            # Create a dedicated connection for pub/sub
            self._redis_pubsub = cache._client.pubsub()
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._redis_pubsub.subscribe,
                WEBSOCKET_CHANNEL
            )

            # Start listener task
            self._pubsub_task = asyncio.create_task(self._pubsub_listener())
            logger.info(f"Redis Pub/Sub started (instance={self._instance_id})")

        except (RedisError, ConnectionError) as e:
            logger.error(f"Redis error starting Pub/Sub: {e}")
            self._redis_pubsub = None

    async def stop_pubsub(self):
        """Stop Redis Pub/Sub listener"""
        if self._pubsub_task:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass

        if self._redis_pubsub:
            try:
                self._redis_pubsub.unsubscribe(WEBSOCKET_CHANNEL)
                self._redis_pubsub.close()
            except (RedisError, ConnectionError, OSError):
                pass  # Best-effort cleanup during shutdown

        logger.info("Redis Pub/Sub stopped")

    async def _pubsub_listener(self):
        """
        Listen for messages from other instances via Redis Pub/Sub.

        Messages are JSON with format:
        {
            "instance_id": "abc123",  # Source instance
            "target_type": "parent" | "all",
            "target_id": "parent_id" | null,
            "message": { ... }
        }
        """
        try:
            while True:
                message = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._redis_pubsub.get_message,
                    True,  # ignore_subscribe_messages
                    0.1    # timeout
                )

                if message and message['type'] == 'message':
                    try:
                        data = json.loads(message['data'])

                        # Skip messages from this instance
                        if data.get('instance_id') == self._instance_id:
                            continue

                        # Route message to local connections
                        target_type = data.get('target_type')
                        payload = data.get('message')

                        if target_type == 'parent':
                            target_id = data.get('target_id')
                            await self._local_broadcast_to_parent(target_id, payload)
                        elif target_type == 'all':
                            await self._local_broadcast_all(payload)

                    except json.JSONDecodeError:
                        logger.error("Invalid JSON in Pub/Sub message")
                    except (ConnectionError, RuntimeError) as e:
                        logger.error(f"Connection error processing Pub/Sub message: {e}")
                    except Exception as e:
                        logger.error(f"Error processing Pub/Sub message: {e}")

                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            pass
        except (ConnectionError, RuntimeError) as e:
            logger.error(f"Pub/Sub listener connection error: {e}")
        except Exception as e:
            logger.exception(f"Unexpected Pub/Sub listener error: {e}")

    async def _publish_to_redis(self, target_type: str, target_id: Optional[str], message: Dict[str, Any]):
        """
        Publish a message to Redis for other instances.

        Args:
            target_type: 'parent' or 'all'
            target_id: Parent ID (if target_type is 'parent')
            message: Message payload
        """
        if not self._redis_pubsub:
            return

        try:
            from utils.cache import cache
            payload = json.dumps({
                'instance_id': self._instance_id,
                'target_type': target_type,
                'target_id': target_id,
                'message': message
            })

            await asyncio.get_event_loop().run_in_executor(
                None,
                cache._client.publish,
                WEBSOCKET_CHANNEL,
                payload
            )
        except (RedisError, ConnectionError) as e:
            logger.error(f"Redis error publishing message: {e}")

    async def connect(
        self,
        websocket: WebSocket,
        parent_id: str,
        connection_id: Optional[str] = None
    ) -> str:
        """
        Register an already-accepted WebSocket connection.

        Note: The caller is responsible for accepting the WebSocket connection
        before calling this method (websocket.accept() must be called first).

        Args:
            websocket: WebSocket connection (must already be accepted)
            parent_id: Parent user ID
            connection_id: Optional connection ID (generated if not provided)

        Returns:
            connection_id: Unique connection identifier
        """
        if connection_id is None:
            connection_id = str(uuid.uuid4())

        async with self._lock:
            # Add to parent connections
            if parent_id not in self.parent_connections:
                self.parent_connections[parent_id] = set()
            self.parent_connections[parent_id].add(websocket)

            # Store metadata
            self.connection_metadata[websocket] = {
                "connection_id": connection_id,
                "parent_id": parent_id,
                "connected_at": datetime.now(timezone.utc),
                "last_heartbeat": datetime.now(timezone.utc)
            }

            # Store connection ID mapping
            self.connection_ids[connection_id] = websocket

        logger.info(f"WebSocket connected: parent={parent_id}, connection_id={connection_id}")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connection_established",
            "connection_id": connection_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Real-time monitoring active"
        })

        return connection_id

    async def disconnect(self, websocket: WebSocket):
        """
        Remove a WebSocket connection

        Args:
            websocket: WebSocket connection to remove
        """
        async with self._lock:
            # Get metadata
            metadata = self.connection_metadata.get(websocket)
            if not metadata:
                return

            parent_id = metadata["parent_id"]
            connection_id = metadata["connection_id"]

            # Remove from parent connections
            if parent_id in self.parent_connections:
                self.parent_connections[parent_id].discard(websocket)

                # Clean up empty sets
                if not self.parent_connections[parent_id]:
                    del self.parent_connections[parent_id]

            # Remove metadata
            del self.connection_metadata[websocket]

            # Remove connection ID mapping
            if connection_id in self.connection_ids:
                del self.connection_ids[connection_id]

        logger.info(f"WebSocket disconnected: parent={parent_id}, connection_id={connection_id}")

    async def send_personal_message(self, websocket: WebSocket, message: Dict[str, Any]):
        """
        Send message to a specific WebSocket connection

        Args:
            websocket: Target WebSocket
            message: Message data (will be JSON serialized)
        """
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(message)
        except (ConnectionError, RuntimeError) as e:
            logger.error(f"Connection error sending message to websocket: {e}")
            await self.disconnect(websocket)
        except Exception as e:
            logger.error(f"Failed to send message to websocket: {e}")
            await self.disconnect(websocket)

    async def _local_broadcast_to_parent(self, parent_id: str, message: Dict[str, Any]):
        """
        Broadcast message to local connections of a specific parent.

        Internal method - use broadcast_to_parent for cross-instance support.
        """
        if parent_id not in self.parent_connections:
            return

        # Get copy of connections to avoid modification during iteration
        connections = list(self.parent_connections.get(parent_id, set()))

        disconnected = []
        for websocket in connections:
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_json(message)
                else:
                    disconnected.append(websocket)
            except (ConnectionError, RuntimeError) as e:
                logger.error(f"Connection error broadcasting to parent {parent_id}: {e}")
                disconnected.append(websocket)
            except Exception as e:
                logger.error(f"Error broadcasting to parent {parent_id}: {e}")
                disconnected.append(websocket)

        # Clean up disconnected websockets
        for websocket in disconnected:
            await self.disconnect(websocket)

        return len(connections) - len(disconnected)

    async def broadcast_to_parent(self, parent_id: str, message: Dict[str, Any]):
        """
        Broadcast message to all connections of a specific parent (across all instances).

        Args:
            parent_id: Parent user ID
            message: Message data (will be JSON serialized)
        """
        # Broadcast to local connections
        local_count = await self._local_broadcast_to_parent(parent_id, message)

        if local_count is None:
            local_count = 0

        # Publish to Redis for other instances
        await self._publish_to_redis('parent', parent_id, message)

        logger.debug(f"Broadcasted message to {local_count} local connections for parent {parent_id}")

    async def _local_broadcast_all(self, message: Dict[str, Any]):
        """
        Broadcast message to all local connections.

        Internal method - use broadcast_all for cross-instance support.
        """
        all_connections = []
        for connections in self.parent_connections.values():
            all_connections.extend(connections)

        disconnected = []
        for websocket in all_connections:
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_json(message)
                else:
                    disconnected.append(websocket)
            except (ConnectionError, RuntimeError) as e:
                logger.error(f"Connection error in broadcast_all: {e}")
                disconnected.append(websocket)
            except Exception as e:
                logger.error(f"Error in broadcast_all: {e}")
                disconnected.append(websocket)

        # Clean up disconnected websockets
        for websocket in disconnected:
            await self.disconnect(websocket)

        return len(all_connections) - len(disconnected)

    async def broadcast_all(self, message: Dict[str, Any]):
        """
        Broadcast message to all connected parents (across all instances).

        Args:
            message: Message data (will be JSON serialized)
        """
        # Broadcast to local connections
        local_count = await self._local_broadcast_all(message)

        # Publish to Redis for other instances
        await self._publish_to_redis('all', None, message)

        logger.debug(f"Broadcasted message to {local_count} local connections (all parents)")

    def get_active_connections(self, parent_id: Optional[str] = None) -> int:
        """
        Get count of active connections

        Args:
            parent_id: If provided, count for specific parent. Otherwise, total count.

        Returns:
            Number of active connections
        """
        if parent_id:
            return len(self.parent_connections.get(parent_id, set()))

        return sum(len(conns) for conns in self.parent_connections.values())

    def is_parent_connected(self, parent_id: str) -> bool:
        """
        Check if a parent has any active connections

        Args:
            parent_id: Parent user ID

        Returns:
            True if parent has at least one active connection
        """
        return parent_id in self.parent_connections and len(self.parent_connections[parent_id]) > 0


# Global connection manager instance
websocket_manager = ConnectionManager()


# ============================================================================
# WEBSOCKET ENDPOINTS
# ============================================================================

async def authenticate_websocket(token: str) -> Optional[AuthSession]:
    """
    Authenticate WebSocket connection via token string.

    Used by the first-message auth pattern where the client sends
    {"type": "auth", "token": "..."} as the first message after connecting.

    Args:
        token: Session token string

    Returns:
        AuthSession if authenticated, None otherwise
    """
    try:
        if not token:
            logger.warning("WebSocket auth attempted without token")
            return None

        is_valid, session = auth_manager.validate_session(token)

        if not is_valid or not session:
            logger.warning("Invalid or inactive session token for WebSocket")
            return None

        return session

    except (ConnectionError, RuntimeError) as e:
        logger.error(f"Connection error during WebSocket auth: {e}")
        return None
    except Exception as e:
        logger.error(f"WebSocket authentication failed: {e}")
        return None


async def get_websocket_session(websocket: WebSocket) -> Optional[AuthSession]:
    """Legacy wrapper — reads token from query params (deprecated)"""
    token = websocket.query_params.get("token")
    return await authenticate_websocket(token)


async def handle_heartbeat(websocket: WebSocket):
    """
    Update last heartbeat timestamp for connection health monitoring

    Args:
        websocket: WebSocket connection
    """
    metadata = websocket_manager.connection_metadata.get(websocket)
    if metadata:
        metadata["last_heartbeat"] = datetime.now(timezone.utc)


async def handle_websocket_message(websocket: WebSocket, data: Dict[str, Any]):
    """
    Handle incoming WebSocket messages

    Args:
        websocket: WebSocket connection
        data: Parsed message data
    """
    message_type = data.get("type")

    if message_type == "ping":
        await handle_heartbeat(websocket)
        await websocket_manager.send_personal_message(websocket, {
            "type": "pong",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    elif message_type == "subscribe_profile":
        # Handle profile-specific subscription (future feature)
        profile_id = data.get("profile_id")
        await websocket_manager.send_personal_message(websocket, {
            "type": "subscribed",
            "profile_id": profile_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    elif message_type == "get_status":
        # Send connection status
        metadata = websocket_manager.connection_metadata.get(websocket)
        await websocket_manager.send_personal_message(websocket, {
            "type": "status",
            "connected": True,
            "connection_id": metadata.get("connection_id") if metadata else None,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    else:
        logger.warning(f"Unknown WebSocket message type: {message_type}")


# ============================================================================
# BROADCAST HELPERS
# ============================================================================

async def broadcast_safety_incident(parent_id: str, incident_data: Dict[str, Any]):
    """
    Broadcast safety incident to parent's active connections

    Args:
        parent_id: Parent user ID
        incident_data: Incident information
    """
    message = {
        "type": "safety_incident",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": incident_data
    }

    await websocket_manager.broadcast_to_parent(parent_id, message)
    logger.info(f"Safety incident broadcasted to parent {parent_id}")


async def broadcast_safety_alert(parent_id: str, alert_data: Dict[str, Any]):
    """
    Broadcast safety alert to parent's active connections

    Args:
        parent_id: Parent user ID
        alert_data: Alert information
    """
    message = {
        "type": "safety_alert",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": alert_data,
        "priority": "high"
    }

    await websocket_manager.broadcast_to_parent(parent_id, message)
    logger.warning(f"Safety alert broadcasted to parent {parent_id}")


async def broadcast_profile_activity(parent_id: str, profile_id: str, activity_data: Dict[str, Any]):
    """
    Broadcast profile activity update to parent

    Args:
        parent_id: Parent user ID
        profile_id: Child profile ID
        activity_data: Activity information
    """
    message = {
        "type": "profile_activity",
        "profile_id": profile_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": activity_data
    }

    await websocket_manager.broadcast_to_parent(parent_id, message)


__all__ = [
    'websocket_manager',
    'ConnectionManager',
    'authenticate_websocket',
    'get_websocket_session',
    'handle_websocket_message',
    'broadcast_safety_incident',
    'broadcast_safety_alert',
    'broadcast_profile_activity',
    'REDIS_PUBSUB_ENABLED'
]
