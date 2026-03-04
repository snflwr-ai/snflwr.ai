# snflwr.ai API Usage Examples

This document provides practical examples for using the snflwr.ai API.

## Base URL

**Local/USB Deployment:**
```
http://localhost:8000
```

**Enterprise Deployment:**
```
https://api.snflwr.example.com
```

## Authentication

### 1. Register a Parent Account

**Endpoint:** `POST /auth/register`

**Request:**
```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "password": "SecureP@ss123!",
    "email": "john@example.com"
  }'
```

**Response:**
```json
{
  "user_id": 1,
  "username": "john_doe",
  "email": "john@example.com",
  "role": "parent",
  "created_at": "2025-12-27T10:30:00Z"
}
```

### 2. Login (Authenticate)

**Endpoint:** `POST /auth/login`

**Request:**
```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "password": "SecureP@ss123!"
  }'
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": 1,
    "username": "john_doe",
    "role": "parent"
  }
}
```

**Store the token for subsequent requests:**
```bash
export TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### 3. Verify Token

**Endpoint:** `GET /auth/verify`

**Request:**
```bash
curl -X GET http://localhost:8000/auth/verify \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "valid": true,
  "user_id": 1,
  "username": "john_doe",
  "role": "parent",
  "expires_at": "2025-12-28T10:30:00Z"
}
```

## Child Profile Management

### 4. Create a Child Profile

**Endpoint:** `POST /parent/children`

**Request:**
```bash
curl -X POST http://localhost:8000/parent/children \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Emma",
    "age": 8,
    "safety_level": "strict",
    "daily_time_limit": 3600,
    "allowed_topics": ["science", "math", "animals"]
  }'
```

**Response:**
```json
{
  "child_id": 1,
  "name": "Emma",
  "age": 8,
  "safety_level": "strict",
  "daily_time_limit": 3600,
  "allowed_topics": ["science", "math", "animals"],
  "created_at": "2025-12-27T10:35:00Z"
}
```

### 5. List All Children

**Endpoint:** `GET /parent/children`

**Request:**
```bash
curl -X GET http://localhost:8000/parent/children \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "children": [
    {
      "child_id": 1,
      "name": "Emma",
      "age": 8,
      "safety_level": "strict",
      "created_at": "2025-12-27T10:35:00Z"
    },
    {
      "child_id": 2,
      "name": "Noah",
      "age": 11,
      "safety_level": "moderate",
      "created_at": "2025-12-27T10:40:00Z"
    }
  ],
  "total": 2
}
```

### 6. Update Child Settings

**Endpoint:** `PUT /parent/children/{child_id}`

**Request:**
```bash
curl -X PUT http://localhost:8000/parent/children/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "safety_level": "moderate",
    "daily_time_limit": 5400
  }'
```

**Response:**
```json
{
  "child_id": 1,
  "name": "Emma",
  "age": 8,
  "safety_level": "moderate",
  "daily_time_limit": 5400,
  "updated_at": "2025-12-27T11:00:00Z"
}
```

## Chat Sessions (Child Interface)

### 7. Start a Chat Session

**Endpoint:** `POST /child/chat/start`

**Request:**
```bash
curl -X POST http://localhost:8000/child/chat/start \
  -H "Content-Type: application/json" \
  -d '{
    "child_id": 1
  }'
```

**Response:**
```json
{
  "session_id": "abc123def456",
  "child_id": 1,
  "started_at": "2025-12-27T11:05:00Z",
  "status": "active",
  "greeting": "Hi Emma! What would you like to talk about today?"
}
```

### 8. Send a Message

**Endpoint:** `POST /child/chat/message`

**Request:**
```bash
curl -X POST http://localhost:8000/child/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "abc123def456",
    "message": "Can you tell me about dolphins?"
  }'
```

**Response (Safe Content):**
```json
{
  "session_id": "abc123def456",
  "user_message": "Can you tell me about dolphins?",
  "assistant_response": "Dolphins are amazing marine mammals! They're very intelligent and live in groups called pods. Dolphins use echolocation to find food and navigate...",
  "safety_status": "safe",
  "timestamp": "2025-12-27T11:06:00Z"
}
```

**Response (Unsafe Content Detected):**
```json
{
  "session_id": "abc123def456",
  "user_message": "[filtered]",
  "assistant_response": "I noticed your message contained content that might not be appropriate. Let's talk about something else! What else would you like to learn about?",
  "safety_status": "blocked",
  "incident_id": 42,
  "timestamp": "2025-12-27T11:10:00Z"
}
```

### 9. Get Conversation History

**Endpoint:** `GET /child/chat/history/{session_id}`

**Request:**
```bash
curl -X GET http://localhost:8000/child/chat/history/abc123def456 \
  -H "Content-Type: application/json"
```

**Response:**
```json
{
  "session_id": "abc123def456",
  "child_id": 1,
  "started_at": "2025-12-27T11:05:00Z",
  "messages": [
    {
      "role": "assistant",
      "content": "Hi Emma! What would you like to talk about today?",
      "timestamp": "2025-12-27T11:05:00Z"
    },
    {
      "role": "user",
      "content": "Can you tell me about dolphins?",
      "timestamp": "2025-12-27T11:06:00Z"
    },
    {
      "role": "assistant",
      "content": "Dolphins are amazing marine mammals!...",
      "timestamp": "2025-12-27T11:06:15Z"
    }
  ],
  "message_count": 3
}
```

### 10. End a Chat Session

**Endpoint:** `POST /child/chat/end`

**Request:**
```bash
curl -X POST http://localhost:8000/child/chat/end \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "abc123def456"
  }'
```

**Response:**
```json
{
  "session_id": "abc123def456",
  "status": "ended",
  "duration_seconds": 450,
  "message_count": 12,
  "ended_at": "2025-12-27T11:15:00Z"
}
```

## Parent Dashboard

### 11. Get All Conversations for a Child

**Endpoint:** `GET /parent/children/{child_id}/conversations`

**Request:**
```bash
curl -X GET http://localhost:8000/parent/children/1/conversations \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "child_id": 1,
  "child_name": "Emma",
  "conversations": [
    {
      "session_id": "abc123def456",
      "started_at": "2025-12-27T11:05:00Z",
      "ended_at": "2025-12-27T11:15:00Z",
      "message_count": 12,
      "flagged_count": 0,
      "duration_seconds": 450
    },
    {
      "session_id": "xyz789ghi012",
      "started_at": "2025-12-26T14:20:00Z",
      "ended_at": "2025-12-26T14:35:00Z",
      "message_count": 8,
      "flagged_count": 1,
      "duration_seconds": 900
    }
  ],
  "total": 2
}
```

### 12. Get Safety Incidents

**Endpoint:** `GET /parent/safety/incidents`

**Request:**
```bash
curl -X GET http://localhost:8000/parent/safety/incidents \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "incidents": [
    {
      "incident_id": 42,
      "child_id": 1,
      "child_name": "Emma",
      "session_id": "abc123def456",
      "incident_type": "inappropriate_language",
      "severity": "medium",
      "details": "Keyword match: profanity detected",
      "timestamp": "2025-12-27T11:10:00Z",
      "acknowledged": false
    },
    {
      "incident_id": 41,
      "child_id": 2,
      "child_name": "Noah",
      "session_id": "xyz789ghi012",
      "incident_type": "personal_info_request",
      "severity": "high",
      "details": "AI requested home address",
      "timestamp": "2025-12-26T14:25:00Z",
      "acknowledged": true
    }
  ],
  "total": 2,
  "unacknowledged_count": 1
}
```

### 13. Acknowledge Safety Incident

**Endpoint:** `POST /parent/safety/incidents/{incident_id}/acknowledge`

**Request:**
```bash
curl -X POST http://localhost:8000/parent/safety/incidents/42/acknowledge \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "notes": "Discussed with Emma about appropriate language"
  }'
```

**Response:**
```json
{
  "incident_id": 42,
  "acknowledged": true,
  "acknowledged_at": "2025-12-27T12:00:00Z",
  "notes": "Discussed with Emma about appropriate language"
}
```

### 14. Get Usage Statistics

**Endpoint:** `GET /parent/children/{child_id}/usage`

**Request:**
```bash
curl -X GET http://localhost:8000/parent/children/1/usage \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "child_id": 1,
  "child_name": "Emma",
  "daily_time_limit": 3600,
  "usage": {
    "today": {
      "date": "2025-12-27",
      "seconds_used": 1250,
      "seconds_remaining": 2350,
      "sessions": 3,
      "messages": 42
    },
    "this_week": {
      "start_date": "2025-12-22",
      "end_date": "2025-12-28",
      "total_seconds": 8400,
      "average_daily_seconds": 1200,
      "total_sessions": 15,
      "total_messages": 210
    },
    "this_month": {
      "month": "2025-12",
      "total_seconds": 32400,
      "total_sessions": 52,
      "total_messages": 780
    }
  }
}
```

### 15. Get Parent Alerts

**Endpoint:** `GET /parent/alerts`

**Request:**
```bash
curl -X GET http://localhost:8000/parent/alerts \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "alerts": [
    {
      "alert_id": 101,
      "child_id": 1,
      "child_name": "Emma",
      "alert_type": "safety_incident",
      "incident_id": 42,
      "message": "Safety filter blocked inappropriate content",
      "severity": "medium",
      "read": false,
      "created_at": "2025-12-27T11:10:05Z"
    },
    {
      "alert_id": 100,
      "child_id": 1,
      "child_name": "Emma",
      "alert_type": "time_limit_warning",
      "message": "Emma has used 75% of daily time limit",
      "severity": "low",
      "read": true,
      "created_at": "2025-12-27T10:45:00Z"
    }
  ],
  "total": 2,
  "unread_count": 1
}
```

## Admin Endpoints

### 16. Get System Statistics

**Endpoint:** `GET /admin/stats`

**Request:**
```bash
curl -X GET http://localhost:8000/admin/stats \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Response:**
```json
{
  "total_users": 145,
  "total_children": 312,
  "active_sessions": 23,
  "total_messages_today": 1842,
  "safety_incidents_today": 5,
  "database_size_mb": 248.5,
  "uptime_seconds": 432000,
  "ollama_status": "healthy",
  "version": "1.0.0"
}
```

## Error Responses

### Authentication Error
```json
{
  "detail": "Invalid credentials",
  "error_code": "AUTH_FAILED",
  "status_code": 401
}
```

### Authorization Error
```json
{
  "detail": "Insufficient permissions",
  "error_code": "FORBIDDEN",
  "status_code": 403
}
```

### Validation Error
```json
{
  "detail": [
    {
      "loc": ["body", "age"],
      "msg": "ensure this value is greater than 0",
      "type": "value_error.number.not_gt"
    }
  ],
  "error_code": "VALIDATION_ERROR",
  "status_code": 422
}
```

### Rate Limit Error
```json
{
  "detail": "Rate limit exceeded: 100 requests per minute",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 45,
  "status_code": 429
}
```

### Safety Filter Block
```json
{
  "detail": "Content blocked by safety filter",
  "error_code": "SAFETY_VIOLATION",
  "incident_id": 42,
  "status_code": 400
}
```

## Python SDK Example

```python
import requests

class SnflwrClient:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.token = None

    def login(self, username, password):
        """Authenticate and store token"""
        response = requests.post(
            f"{self.base_url}/auth/login",
            json={"username": username, "password": password}
        )
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        return data

    def _headers(self):
        """Get auth headers"""
        return {"Authorization": f"Bearer {self.token}"}

    def create_child(self, name, age, safety_level="strict"):
        """Create a new child profile"""
        response = requests.post(
            f"{self.base_url}/parent/children",
            headers=self._headers(),
            json={
                "name": name,
                "age": age,
                "safety_level": safety_level
            }
        )
        response.raise_for_status()
        return response.json()

    def start_chat(self, child_id):
        """Start a chat session"""
        response = requests.post(
            f"{self.base_url}/child/chat/start",
            json={"child_id": child_id}
        )
        response.raise_for_status()
        return response.json()

    def send_message(self, session_id, message):
        """Send a message in a chat session"""
        response = requests.post(
            f"{self.base_url}/child/chat/message",
            json={
                "session_id": session_id,
                "message": message
            }
        )
        response.raise_for_status()
        return response.json()

    def get_incidents(self):
        """Get all safety incidents"""
        response = requests.get(
            f"{self.base_url}/parent/safety/incidents",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()


# Usage example
client = SnflwrClient()

# Login as parent
client.login("john_doe", "SecureP@ss123!")

# Create child profile
child = client.create_child("Emma", 8, "strict")
print(f"Created child: {child['name']} (ID: {child['child_id']})")

# Start chat session
session = client.start_chat(child["child_id"])
print(f"Session started: {session['session_id']}")

# Send messages
response = client.send_message(
    session["session_id"],
    "Can you tell me about space?"
)
print(f"AI: {response['assistant_response']}")

# Check safety incidents
incidents = client.get_incidents()
print(f"Unacknowledged incidents: {incidents['unacknowledged_count']}")
```

## JavaScript/Node.js Example

```javascript
const axios = require('axios');

class SnflwrClient {
  constructor(baseURL = 'http://localhost:8000') {
    this.baseURL = baseURL;
    this.token = null;
  }

  async login(username, password) {
    const response = await axios.post(`${this.baseURL}/auth/login`, {
      username,
      password
    });
    this.token = response.data.access_token;
    return response.data;
  }

  _headers() {
    return { Authorization: `Bearer ${this.token}` };
  }

  async createChild(name, age, safetyLevel = 'strict') {
    const response = await axios.post(
      `${this.baseURL}/parent/children`,
      { name, age, safety_level: safetyLevel },
      { headers: this._headers() }
    );
    return response.data;
  }

  async startChat(childId) {
    const response = await axios.post(
      `${this.baseURL}/child/chat/start`,
      { child_id: childId }
    );
    return response.data;
  }

  async sendMessage(sessionId, message) {
    const response = await axios.post(
      `${this.baseURL}/child/chat/message`,
      { session_id: sessionId, message }
    );
    return response.data;
  }

  async getIncidents() {
    const response = await axios.get(
      `${this.baseURL}/parent/safety/incidents`,
      { headers: this._headers() }
    );
    return response.data;
  }
}

// Usage
(async () => {
  const client = new SnflwrClient();
  
  await client.login('john_doe', 'SecureP@ss123!');
  
  const child = await client.createChild('Emma', 8, 'strict');
  console.log(`Created child: ${child.name} (ID: ${child.child_id})`);
  
  const session = await client.startChat(child.child_id);
  console.log(`Session started: ${session.session_id}`);
  
  const response = await client.sendMessage(
    session.session_id,
    'Can you tell me about space?'
  );
  console.log(`AI: ${response.assistant_response}`);
})();
```

## Additional Resources

- **OpenAPI Documentation:** http://localhost:8000/docs (Swagger UI)
- **ReDoc Documentation:** http://localhost:8000/redoc
- **OpenAPI Schema (JSON):** http://localhost:8000/openapi.json
- **Health Check:** http://localhost:8000/health

## Rate Limits

- **Default:** 100 requests per minute per IP address
- **Authenticated:** 300 requests per minute per user
- **Admin:** 1000 requests per minute

## Webhooks (Enterprise Feature)

Configure webhooks to receive real-time notifications:

```json
POST /admin/webhooks
{
  "url": "https://your-app.com/webhooks/snflwr",
  "events": ["safety_incident", "time_limit_reached"],
  "secret": "your-webhook-secret"
}
```

Webhook payload example:
```json
{
  "event": "safety_incident",
  "timestamp": "2025-12-27T11:10:05Z",
  "data": {
    "incident_id": 42,
    "child_id": 1,
    "severity": "medium",
    "incident_type": "inappropriate_language"
  },
  "signature": "sha256=..."
}
```
