#!/bin/bash
#
# Test Authentication API Endpoints
# Start the API server first: uvicorn api.server:app --reload
#

BASE_URL="http://localhost:39150"

echo "============================================================"
echo "Testing snflwr.ai Authentication API"
echo "============================================================"

# Test 1: Register a new parent
echo ""
echo "1. Testing Parent Registration..."
REGISTER_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "api_test@example.com",
    "password": "TestPassword123",
    "verify_password": "TestPassword123"
  }')

echo "Response: $REGISTER_RESPONSE"

if echo "$REGISTER_RESPONSE" | grep -q "success"; then
  echo "[OK] Registration successful!"
  USER_ID=$(echo "$REGISTER_RESPONSE" | grep -o '"user_id":"[^"]*"' | cut -d'"' -f4)
  echo "  User ID: $USER_ID"
else
  echo "[OK] User might already exist (expected on rerun)"
fi

# Test 2: Login
echo ""
echo "2. Testing Login..."
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "api_test@example.com",
    "password": "TestPassword123"
  }')

echo "Response: $LOGIN_RESPONSE"

if echo "$LOGIN_RESPONSE" | grep -q "token"; then
  echo "[OK] Login successful!"
  TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  echo "  Token: ${TOKEN:0:32}..."
else
  echo "[FAIL] Login failed"
  exit 1
fi

# Test 3: Validate Session
echo ""
echo "3. Testing Session Validation..."
VALIDATE_RESPONSE=$(curl -s -X GET "$BASE_URL/api/auth/validate/$TOKEN")

echo "Response: $VALIDATE_RESPONSE"

if echo "$VALIDATE_RESPONSE" | grep -q "valid"; then
  echo "[OK] Session validation successful!"
else
  echo "[FAIL] Session validation failed"
fi

# Test 4: Login with wrong password (should fail)
echo ""
echo "4. Testing Login with Wrong Password (should fail)..."
WRONG_LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "api_test@example.com",
    "password": "WrongPassword"
  }')

if echo "$WRONG_LOGIN_RESPONSE" | grep -q "detail"; then
  echo "[OK] Correctly rejected wrong password!"
else
  echo "[FAIL] Should have rejected wrong password"
fi

# Test 5: Logout
echo ""
echo "5. Testing Logout..."
LOGOUT_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/logout?session_id=$TOKEN")

echo "Response: $LOGOUT_RESPONSE"

if echo "$LOGOUT_RESPONSE" | grep -q "success"; then
  echo "[OK] Logout successful!"
else
  echo "Note: Logout response: $LOGOUT_RESPONSE"
fi

echo ""
echo "============================================================"
echo "[OK] API Authentication Tests Completed!"
echo "============================================================"
echo ""
echo "To start the API server:"
echo "  uvicorn api.server:app --reload --port 39150"
