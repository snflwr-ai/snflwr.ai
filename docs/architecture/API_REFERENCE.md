# snflwr.ai API Reference

## Overview

snflwr.ai provides a RESTful API for managing K-12 safe AI learning platform. This API enforces backend safety monitoring that cannot be bypassed by students.

**Base URL:** `http://localhost:8000` (development) / `https://your-domain.com` (production)

**API Version:** 0.1.0

**Authentication:** JWT Bearer tokens

---

## Table of Contents

1. [Authentication](#authentication)
2. [Users & Parents](#users--parents)
3. [Child Profiles](#child-profiles)
4. [Chat & Conversations](#chat--conversations)
5. [Safety & Monitoring](#safety--monitoring)
6. [Analytics](#analytics)
7. [Admin](#admin)
8. [Monitoring](#monitoring)
9. [Error Handling](#error-handling)
10. [Rate Limiting](#rate-limiting)

---

## Interactive Documentation

snflwr.ai provides auto-generated interactive API documentation powered by FastAPI:

### Swagger UI (ReDoc)
**URL:** `http://localhost:8000/docs`

Features:
- Browse all endpoints
- Try out API calls directly
- View request/response schemas
- See authentication requirements

### OpenAPI Specification
**URL:** `http://localhost:8000/openapi.json`

Download the OpenAPI 3.0 specification for:
- API client generation
- Testing automation
- Integration with tools like Postman

**Export OpenAPI spec:**
```bash
# Download OpenAPI JSON
curl http://localhost:8000/openapi.json > snflwr-api-spec.json

# Generate client (example with openapi-generator)
openapi-generator-cli generate \
  -i snflwr-api-spec.json \
  -g python \
  -o ./client
```

---

## Authentication

### Register Parent Account

Create a new parent account.

**Endpoint:** `POST /api/auth/register`

**Request Body:**
```json
{
  "email": "parent@example.com",
  "password": "SecurePassword123",
  "name": "Parent Name"
}
```

**Response:** `201 Created`
```json
{
  "status": "success",
  "message": "Registration successful! Please check your email to verify your account.",
  "user_id": "user_abc123"
}
```

**Email Verification Required:** Check email for verification link.

---

### Login

Authenticate and receive JWT token.

**Endpoint:** `POST /api/auth/login`

**Request Body:**
```json
{
  "email": "parent@example.com",
  "password": "SecurePassword123"
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user_id": "user_abc123",
  "role": "parent"
}
```

**Use token in subsequent requests:**
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

---

### Verify Email

Verify email address with token received in email.

**Endpoint:** `POST /api/auth/verify-email`

**Request Body:**
```json
{
  "token": "verification_token_from_email"
}
```

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Email verified successfully!"
}
```

---

### Password Reset

Request password reset email.

**Endpoint:** `POST /api/auth/forgot-password`

**Request Body:**
```json
{
  "email": "parent@example.com"
}
```

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "If an account exists with this email, password reset instructions have been sent."
}
```

**Reset password with token:**

**Endpoint:** `POST /api/auth/reset-password`

**Request Body:**
```json
{
  "token": "reset_token_from_email",
  "new_password": "NewSecurePassword123",
  "verify_password": "NewSecurePassword123"
}
```

---

## Child Profiles

### Create Child Profile

Create a new child profile.

**Endpoint:** `POST /api/profiles/create`

**Auth Required:** Yes (Parent/Admin)

**Request Body:**
```json
{
  "name": "Child Name",
  "age": 10,
  "grade_level": "5th",
  "tier": "standard",
  "model_role": "student"
}
```

**Fields:**
- `tier`: "budget" | "standard" | "premium"
- `model_role`: "student" | "educator"
- `grade_level`: "K", "1st", "2nd", ... "12th"

**Response:** `201 Created`
```json
{
  "status": "success",
  "profile_id": "profile_xyz789",
  "message": "Child profile created successfully"
}
```

---

### List Child Profiles

Get all child profiles for authenticated parent.

**Endpoint:** `GET /api/profiles/list`

**Auth Required:** Yes

**Response:** `200 OK`
```json
{
  "profiles": [
    {
      "profile_id": "profile_xyz789",
      "name": "Child Name",
      "age": 10,
      "grade_level": "5th",
      "tier": "standard",
      "is_active": true,
      "created_at": "2025-12-20T10:00:00Z"
    }
  ]
}
```

---

### Get Profile Details

Get detailed information for a specific profile.

**Endpoint:** `GET /api/profiles/{profile_id}`

**Auth Required:** Yes (must own profile)

**Response:** `200 OK`
```json
{
  "profile_id": "profile_xyz789",
  "name": "Child Name",
  "age": 10,
  "grade_level": "5th",
  "tier": "standard",
  "model_role": "student",
  "is_active": true,
  "created_at": "2025-12-20T10:00:00Z",
  "parent_id": "user_abc123",
  "preferences": {}
}
```

---

### Update Profile

Update child profile information.

**Endpoint:** `PUT /api/profiles/{profile_id}`

**Auth Required:** Yes (must own profile)

**Request Body:**
```json
{
  "name": "Updated Name",
  "age": 11,
  "tier": "premium"
}
```

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Profile updated successfully"
}
```

---

### Delete Profile

Deactivate a child profile.

**Endpoint:** `DELETE /api/profiles/{profile_id}`

**Auth Required:** Yes (must own profile)

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Profile deactivated"
}
```

---

## Chat & Conversations

### Send Chat Message

Send a message through the 5-stage safety pipeline.

**Endpoint:** `POST /api/chat/send`

**Auth Required:** Yes

**Request Body:**
```json
{
  "message": "What is photosynthesis?",
  "profile_id": "profile_xyz789",
  "model": "qwen3.5:9b",
  "session_id": "session_123" // optional, will create new if not provided
}
```

**Response:** `200 OK`
```json
{
  "message": "Photosynthesis is the process by which plants...",
  "blocked": false,
  "safety_metadata": {
    "filter_layers_passed": ["keyword", "llm_classifier", "response_validation"],
    "model_used": "qwen3.5:9b",
    "profile_tier": "standard"
  },
  "model": "qwen3.5:9b",
  "timestamp": "2025-12-25T10:30:00Z",
  "session_id": "session_123"
}
```

**Blocked Content Response:**
```json
{
  "message": "I can't help with that topic. Let's focus on your studies instead!",
  "blocked": true,
  "block_reason": "Inappropriate content detected",
  "block_category": "major",
  "safety_metadata": {
    "triggered_keywords": ["inappropriate_term"],
    "filter_layer": "keyword"
  },
  "model": "qwen3.5:9b",
  "timestamp": "2025-12-25T10:30:00Z",
  "session_id": "session_123"
}
```

---

### End Session

End a conversation session.

**Endpoint:** `POST /api/chat/end-session`

**Auth Required:** Yes

**Request Body:**
```json
{
  "session_id": "session_123"
}
```

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Session ended"
}
```

---

## Safety & Monitoring

### Get Safety Incidents

Retrieve safety incidents for a profile.

**Endpoint:** `GET /api/safety/incidents/{profile_id}`

**Auth Required:** Yes (must own profile)

**Query Parameters:**
- `severity` (optional): "minor" | "major" | "critical"
- `limit` (optional): Number of incidents to return (default: 50)
- `offset` (optional): Pagination offset

**Response:** `200 OK`
```json
{
  "incidents": [
    {
      "incident_id": "inc_123",
      "profile_id": "profile_xyz789",
      "incident_type": "prohibited_keyword",
      "severity": "major",
      "content_snippet": "User message that triggered...",
      "timestamp": "2025-12-25T10:15:00Z",
      "parent_notified": true,
      "acknowledged": false
    }
  ],
  "total": 15,
  "page": 1
}
```

---

### Get Parent Alerts

Get unacknowledged parent alerts.

**Endpoint:** `GET /api/safety/alerts`

**Auth Required:** Yes

**Response:** `200 OK`
```json
{
  "alerts": [
    {
      "alert_id": "alert_456",
      "profile_id": "profile_xyz789",
      "severity": "high",
      "incident_count": 3,
      "description": "Multiple safety incidents detected",
      "created_at": "2025-12-25T10:00:00Z",
      "acknowledged": false,
      "requires_action": true
    }
  ]
}
```

---

### Acknowledge Alert

Mark a parent alert as acknowledged.

**Endpoint:** `POST /api/safety/acknowledge/{alert_id}`

**Auth Required:** Yes

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Alert acknowledged"
}
```

---

## Analytics

### Get Usage Statistics

Get usage statistics for a profile.

**Endpoint:** `GET /api/analytics/usage/{profile_id}`

**Auth Required:** Yes (must own profile)

**Query Parameters:**
- `start_date` (optional): Start date (ISO 8601)
- `end_date` (optional): End date (ISO 8601)

**Response:** `200 OK`
```json
{
  "profile_id": "profile_xyz789",
  "period": {
    "start": "2025-12-01T00:00:00Z",
    "end": "2025-12-31T23:59:59Z"
  },
  "statistics": {
    "total_sessions": 45,
    "total_messages": 523,
    "avg_session_duration_minutes": 12.5,
    "safety_incidents": {
      "minor": 5,
      "major": 1,
      "critical": 0
    },
    "daily_average": 17.4
  }
}
```

---

## Admin

### List All Users

Get all users (admin only).

**Endpoint:** `GET /api/admin/users`

**Auth Required:** Yes (Admin only)

**Response:** `200 OK`
```json
{
  "users": [
    {
      "user_id": "user_abc123",
      "email": "parent@example.com",
      "role": "parent",
      "is_active": true,
      "created_at": "2025-12-01T00:00:00Z",
      "child_count": 2
    }
  ],
  "total": 150
}
```

---

### System Statistics

Get system-wide statistics (admin only).

**Endpoint:** `GET /api/admin/stats`

**Auth Required:** Yes (Admin only)

**Response:** `200 OK`
```json
{
  "users": {
    "total": 150,
    "active": 142,
    "parents": 148,
    "admins": 2
  },
  "profiles": {
    "total": 235,
    "active": 220
  },
  "sessions": {
    "active": 12,
    "today": 87
  },
  "safety": {
    "incidents_24h": 23,
    "critical_incidents_7d": 0
  }
}
```

---

## Monitoring

### Health Check

Basic health check for load balancers.

**Endpoint:** `GET /health`

**Auth Required:** No

**Response:** `200 OK`
```json
{
  "status": "healthy",
  "timestamp": "2025-12-25T10:30:00Z",
  "database": "postgresql",
  "safety_monitoring": true
}
```

---

### Detailed Health Check

Component-level health check.

**Endpoint:** `GET /api/health/detailed`

**Auth Required:** No

**Response:** `200 OK`
```json
{
  "status": "healthy",
  "timestamp": "2025-12-25T10:30:00Z",
  "components": {
    "database": {
      "status": "healthy",
      "type": "postgresql"
    },
    "system": {
      "status": "healthy",
      "cpu_percent": 23.5,
      "memory_percent": 45.2,
      "disk_percent": 62.1
    },
    "safety_monitoring": {
      "status": "enabled"
    }
  }
}
```

---

### Prometheus Metrics

Metrics endpoint for Prometheus scraping.

**Endpoint:** `GET /api/metrics`

**Auth Required:** No

**Response:** `200 OK` (Prometheus text format)
```
# HELP snflwr_users_active Number of active users
# TYPE snflwr_users_active gauge
snflwr_users_active 142

# HELP snflwr_safety_incidents_24h Safety incidents in last 24 hours
# TYPE snflwr_safety_incidents_24h gauge
snflwr_safety_incidents_24h 23
```

---

## Error Handling

### Standard Error Response

All errors follow this format:

```json
{
  "detail": "Error message describing what went wrong",
  "timestamp": "2025-12-25T10:30:00Z"
}
```

### HTTP Status Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 200 | OK | Successful request |
| 201 | Created | Resource created successfully |
| 400 | Bad Request | Invalid request parameters |
| 401 | Unauthorized | Missing or invalid authentication |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |
| 503 | Service Unavailable | Ollama or database unavailable |

### Example Error Responses

**401 Unauthorized:**
```json
{
  "detail": "Authentication required",
  "timestamp": "2025-12-25T10:30:00Z"
}
```

**403 Forbidden:**
```json
{
  "detail": "Access denied: You can only access your own children's profiles",
  "timestamp": "2025-12-25T10:30:00Z"
}
```

**429 Rate Limit:**
```json
{
  "detail": "Rate limit exceeded. Please try again later.",
  "timestamp": "2025-12-25T10:30:00Z"
}
```

---

## Rate Limiting

### Rate Limit Headers

Responses include rate limit information in headers:

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 987
X-RateLimit-Reset: 2025-12-25T10:31:00Z
```

### Rate Limit Configuration

| Endpoint Type | Limit | Window |
|--------------|-------|--------|
| Authentication | 10 requests | 60 seconds |
| API (General) | 1000 requests | 60 seconds |
| Chat | 100 requests | 60 seconds |
| Password Reset | 3 requests | 1 hour |
| Email Verification | 5 requests | 1 hour |

### Handling Rate Limits

When rate limited (429), wait for `retry_after` seconds:

```python
import requests
import time

response = requests.post('http://localhost:8000/api/auth/login', json={...})

if response.status_code == 429:
    retry_after = int(response.headers.get('Retry-After', 60))
    print(f"Rate limited. Waiting {retry_after} seconds...")
    time.sleep(retry_after)
    # Retry request
```

---

## Code Examples

### Python Client

```python
import requests

class SnflwrClient:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.token = None

    def login(self, email, password):
        response = requests.post(
            f"{self.base_url}/api/auth/login",
            json={"email": email, "password": password}
        )
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        return data

    def get_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def create_profile(self, name, age, grade_level):
        response = requests.post(
            f"{self.base_url}/api/profiles/create",
            json={
                "name": name,
                "age": age,
                "grade_level": grade_level,
                "tier": "standard",
                "model_role": "student"
            },
            headers=self.get_headers()
        )
        response.raise_for_status()
        return response.json()

    def send_message(self, profile_id, message, session_id=None):
        response = requests.post(
            f"{self.base_url}/api/chat/send",
            json={
                "profile_id": profile_id,
                "message": message,
                "session_id": session_id
            },
            headers=self.get_headers()
        )
        response.raise_for_status()
        return response.json()

# Usage
client = SnflwrClient()
client.login("parent@example.com", "password")
profile = client.create_profile("Child", 10, "5th")
response = client.send_message(profile["profile_id"], "What is 2+2?")
print(response["message"])
```

### JavaScript/TypeScript Client

```typescript
class SnflwrClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string = "http://localhost:8000") {
    this.baseUrl = baseUrl;
  }

  async login(email: string, password: string) {
    const response = await fetch(`${this.baseUrl}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });

    const data = await response.json();
    this.token = data.access_token;
    return data;
  }

  private getHeaders() {
    return {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${this.token}`
    };
  }

  async sendMessage(profileId: string, message: string, sessionId?: string) {
    const response = await fetch(`${this.baseUrl}/api/chat/send`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({
        profile_id: profileId,
        message,
        session_id: sessionId
      })
    });

    return await response.json();
  }
}

// Usage
const client = new SnflwrClient();
await client.login("parent@example.com", "password");
const response = await client.sendMessage("profile_id", "What is photosynthesis?");
console.log(response.message);
```

---

## Webhooks (Future)

Webhook support for real-time safety alerts is planned for a future release.

---

## Support

- **Documentation:** https://docs.snflwr.ai
- **Interactive API Docs:** http://localhost:8000/docs
- **Issues:** https://github.com/tmartin2113/snflwr-ai/issues
- **Support Email:** support@snflwr.ai

---

**API Reference Version:** 1.0
**Last Updated:** 2025-12-25
**snflwr.ai Version:** 0.1.0
