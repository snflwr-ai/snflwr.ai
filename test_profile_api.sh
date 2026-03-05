#!/bin/bash
#
# Test Profile API Endpoints
# Requires API server running: uvicorn api.server:app --reload
#

BASE_URL="http://localhost:39150"

echo "============================================================"
echo "Testing snflwr.ai Profile API"
echo "============================================================"

# First, login to get a token and user_id
echo ""
echo "0. Logging in to get credentials..."
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "api_test@example.com",
    "password": "TestPassword123"
  }')

PARENT_ID=$(echo "$LOGIN_RESPONSE" | grep -o '"user_id":"[^"]*"' | cut -d'"' -f4)
TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)

echo "  Parent ID: $PARENT_ID"
echo "  Token: ${TOKEN:0:32}..."

# Test 1: Create Child Profile (Age 5)
echo ""
echo "1. Creating Child Profile (Age 5 - Kindergarten)..."
CREATE_RESPONSE_1=$(curl -s -X POST "$BASE_URL/api/profiles/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"parent_id\": \"$PARENT_ID\",
    \"name\": \"Emma Smith\",
    \"age\": 5,
    \"grade_level\": \"K\",
    \"tier\": \"standard\",
    \"model_role\": \"student\"
  }")

echo "Response: $CREATE_RESPONSE_1"

if echo "$CREATE_RESPONSE_1" | grep -q "profile_id"; then
  echo "[OK] Profile created successfully!"
  PROFILE_ID_1=$(echo "$CREATE_RESPONSE_1" | grep -o '"profile_id":"[^"]*"' | cut -d'"' -f4)
  echo "  Profile ID: $PROFILE_ID_1"
else
  echo "Note: $CREATE_RESPONSE_1"
fi

# Test 2: Create Child Profile (Age 14)
echo ""
echo "2. Creating Child Profile (Age 14 - 8th Grade)..."
CREATE_RESPONSE_2=$(curl -s -X POST "$BASE_URL/api/profiles/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"parent_id\": \"$PARENT_ID\",
    \"name\": \"Jake Martinez\",
    \"age\": 14,
    \"grade_level\": \"8\",
    \"tier\": \"premium\",
    \"model_role\": \"student\"
  }")

echo "Response: $CREATE_RESPONSE_2"

if echo "$CREATE_RESPONSE_2" | grep -q "profile_id"; then
  echo "[OK] Profile created successfully!"
  PROFILE_ID_2=$(echo "$CREATE_RESPONSE_2" | grep -o '"profile_id":"[^"]*"' | cut -d'"' -f4)
  echo "  Profile ID: $PROFILE_ID_2"
fi

# Test 3: Create Profile with Invalid Age (should fail)
echo ""
echo "3. Creating Profile with Age 25 (should fail)..."
CREATE_RESPONSE_FAIL=$(curl -s -X POST "$BASE_URL/api/profiles/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"parent_id\": \"$PARENT_ID\",
    \"name\": \"Invalid Age\",
    \"age\": 25,
    \"grade_level\": \"12\",
    \"tier\": \"standard\",
    \"model_role\": \"student\"
  }")

echo "Response: $CREATE_RESPONSE_FAIL"

if echo "$CREATE_RESPONSE_FAIL" | grep -q "0 and 18\|CHECK constraint"; then
  echo "[OK] Correctly rejected age 25!"
else
  echo "Note: Response - $CREATE_RESPONSE_FAIL"
fi

# Test 4: Get All Profiles for Parent
echo ""
echo "4. Getting All Profiles for Parent..."
GET_PROFILES_RESPONSE=$(curl -s -X GET "$BASE_URL/api/profiles/parent/$PARENT_ID" \
  -H "Authorization: Bearer $TOKEN")

echo "Response: $GET_PROFILES_RESPONSE"

if echo "$GET_PROFILES_RESPONSE" | grep -q "profiles"; then
  PROFILE_COUNT=$(echo "$GET_PROFILES_RESPONSE" | grep -o '"profile_id"' | wc -l)
  echo "[OK] Retrieved profiles successfully! Count: $PROFILE_COUNT"
else
  echo "Note: $GET_PROFILES_RESPONSE"
fi

# Test 5: Get Specific Profile
if [ ! -z "$PROFILE_ID_1" ]; then
  echo ""
  echo "5. Getting Specific Profile..."
  GET_PROFILE_RESPONSE=$(curl -s -X GET "$BASE_URL/api/profiles/$PROFILE_ID_1" \
    -H "Authorization: Bearer $TOKEN")

  echo "Response: $GET_PROFILE_RESPONSE"

  if echo "$GET_PROFILE_RESPONSE" | grep -q "Emma"; then
    echo "[OK] Retrieved specific profile successfully!"
  fi
fi

# Test 6: Update Profile
if [ ! -z "$PROFILE_ID_1" ]; then
  echo ""
  echo "6. Updating Profile (Change age to 6)..."
  UPDATE_RESPONSE=$(curl -s -X PUT "$BASE_URL/api/profiles/$PROFILE_ID_1" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{
      "age": 6,
      "grade_level": "1"
    }')

  echo "Response: $UPDATE_RESPONSE"

  if echo "$UPDATE_RESPONSE" | grep -q "success"; then
    echo "[OK] Profile updated successfully!"
  else
    echo "Note: $UPDATE_RESPONSE"
  fi
fi

# Test 7: Deactivate Profile
if [ ! -z "$PROFILE_ID_2" ]; then
  echo ""
  echo "7. Deactivating Profile..."
  DEACTIVATE_RESPONSE=$(curl -s -X DELETE "$BASE_URL/api/profiles/$PROFILE_ID_2" \
    -H "Authorization: Bearer $TOKEN")

  echo "Response: $DEACTIVATE_RESPONSE"

  if echo "$DEACTIVATE_RESPONSE" | grep -q "success"; then
    echo "[OK] Profile deactivated successfully!"
  else
    echo "Note: $DEACTIVATE_RESPONSE"
  fi
fi

echo ""
echo "============================================================"
echo "[OK] Profile API Tests Completed!"
echo "============================================================"
