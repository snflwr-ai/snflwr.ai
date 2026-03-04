/**
 * snflwr.ai Load Testing Suite
 *
 * Tests system performance under production load conditions:
 * - 100 concurrent users
 * - 1000 messages/minute
 * - Safety pipeline throughput
 * - Database performance
 * - API response times
 *
 * Usage:
 *   k6 run tests/load/snflwr_load_test.js
 *   k6 run --vus 100 --duration 5m tests/load/snflwr_load_test.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.0.1/index.js';

// Custom metrics
const messageProcessingTime = new Trend('message_processing_time');
const safetyCheckTime = new Trend('safety_check_time');
const dbQueryTime = new Trend('db_query_time');
const failureRate = new Rate('failure_rate');
const messagesProcessed = new Counter('messages_processed');
const incidentsDetected = new Counter('incidents_detected');

// Test configuration
export const options = {
  stages: [
    { duration: '1m', target: 20 },   // Ramp up to 20 users
    { duration: '2m', target: 50 },   // Ramp up to 50 users
    { duration: '2m', target: 100 },  // Ramp up to 100 users
    { duration: '3m', target: 100 },  // Stay at 100 users
    { duration: '1m', target: 50 },   // Ramp down to 50 users
    { duration: '1m', target: 0 },    // Ramp down to 0 users
  ],

  thresholds: {
    // API response time thresholds
    'http_req_duration': ['p(95)<2000', 'p(99)<5000'],  // 95% < 2s, 99% < 5s

    // Message processing thresholds
    'message_processing_time': ['p(95)<3000', 'p(99)<7000'],  // Including safety checks

    // Safety check performance
    'safety_check_time': ['p(95)<1000', 'p(99)<2000'],

    // Success rate thresholds
    'http_req_failed': ['rate<0.05'],  // Less than 5% failures
    'failure_rate': ['rate<0.05'],

    // Database performance
    'db_query_time': ['p(95)<500', 'p(99)<1000'],
  },
};

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-api-key';

// Sample test data
const PARENT_IDS = generateParentIds(20);
const PROFILE_IDS = generateProfileIds(50);
const TEST_MESSAGES = [
  "What is photosynthesis?",
  "How do I solve quadratic equations?",
  "Can you explain the water cycle?",
  "What are the three laws of motion?",
  "Help me understand cell division",
  "How does the respiratory system work?",
  "What is the Pythagorean theorem?",
  "Explain the difference between weather and climate",
  "What causes earthquakes?",
  "How do plants make food?",
  "What is the periodic table?",
  "Can you help me with my math homework?",
  "What are the parts of a cell?",
  "How does gravity work?",
  "What is an ecosystem?"
];

function generateParentIds(count) {
  const ids = [];
  for (let i = 0; i < count; i++) {
    ids.push(`load-test-parent-${i}`);
  }
  return ids;
}

function generateProfileIds(count) {
  const ids = [];
  for (let i = 0; i < count; i++) {
    ids.push(`load-test-profile-${i}`);
  }
  return ids;
}

function getRandomElement(array) {
  return array[Math.floor(Math.random() * array.length)];
}

/**
 * Setup phase - runs once before all VUs
 */
export function setup() {
  console.log('Setting up load test...');
  console.log(`Target: ${BASE_URL}`);
  console.log(`Simulating 100 concurrent users`);
  console.log(`Expected throughput: ~1000 messages/minute`);

  return {
    baseUrl: BASE_URL,
    apiKey: API_KEY
  };
}

/**
 * Main test scenario - runs for each VU
 */
export default function(data) {
  const parentId = getRandomElement(PARENT_IDS);
  const profileId = getRandomElement(PROFILE_IDS);
  const message = getRandomElement(TEST_MESSAGES);

  // Simulate user authentication
  const authResponse = authenticateUser(data.baseUrl, parentId);
  if (!authResponse) {
    failureRate.add(1);
    return;
  }

  const sessionToken = authResponse.session_token;

  // Test 1: Send chat message through safety pipeline
  testChatMessage(data.baseUrl, sessionToken, profileId, message);

  // Test 2: Get profile information
  testGetProfile(data.baseUrl, sessionToken, profileId);

  // Test 3: Check for incidents
  testGetIncidents(data.baseUrl, sessionToken, profileId);

  // Test 4: Get conversation history
  testGetConversations(data.baseUrl, sessionToken, profileId);

  // Realistic think time between requests (1-5 seconds)
  sleep(Math.random() * 4 + 1);
}

/**
 * Authenticate user and get session token
 */
function authenticateUser(baseUrl, parentId) {
  const startTime = Date.now();

  const response = http.post(`${baseUrl}/api/auth/login`, JSON.stringify({
    username: parentId,
    password: 'load-test-password'
  }), {
    headers: { 'Content-Type': 'application/json' }
  });

  const success = check(response, {
    'auth successful': (r) => r.status === 200 || r.status === 201,
  });

  if (!success) {
    console.warn(`Authentication failed for ${parentId}: ${response.status}`);
    failureRate.add(1);
    return null;
  }

  const duration = Date.now() - startTime;
  dbQueryTime.add(duration);

  try {
    return JSON.parse(response.body);
  } catch (e) {
    return { session_token: 'mock-token' };  // Fallback for testing
  }
}

/**
 * Test chat message through safety pipeline
 */
function testChatMessage(baseUrl, sessionToken, profileId, message) {
  const startTime = Date.now();

  const payload = JSON.stringify({
    profile_id: profileId,
    message: message,
    model: 'qwen3.5:9b',
    conversation_id: `conv-${profileId}-${Date.now()}`
  });

  const response = http.post(`${baseUrl}/api/chat`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${sessionToken}`
    },
    timeout: '30s'
  });

  const success = check(response, {
    'chat message sent': (r) => r.status === 200,
    'response has content': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.response && body.response.length > 0;
      } catch (e) {
        return false;
      }
    }
  });

  const duration = Date.now() - startTime;
  messageProcessingTime.add(duration);

  if (success) {
    messagesProcessed.add(1);

    // Check if incident was detected
    try {
      const body = JSON.parse(response.body);
      if (body.safety_incident) {
        incidentsDetected.add(1);
      }
      if (body.safety_check_duration) {
        safetyCheckTime.add(body.safety_check_duration);
      }
    } catch (e) {
      // Ignore parse errors
    }
  } else {
    failureRate.add(1);
    console.warn(`Chat message failed: ${response.status}`);
  }
}

/**
 * Test get profile endpoint
 */
function testGetProfile(baseUrl, sessionToken, profileId) {
  const startTime = Date.now();

  const response = http.get(`${baseUrl}/api/profiles/${profileId}`, {
    headers: { 'Authorization': `Bearer ${sessionToken}` }
  });

  check(response, {
    'profile retrieved': (r) => r.status === 200 || r.status === 404,
  });

  const duration = Date.now() - startTime;
  dbQueryTime.add(duration);
}

/**
 * Test get incidents endpoint
 */
function testGetIncidents(baseUrl, sessionToken, profileId) {
  const startTime = Date.now();

  const response = http.get(`${baseUrl}/api/safety/incidents?profile_id=${profileId}`, {
    headers: { 'Authorization': `Bearer ${sessionToken}` }
  });

  check(response, {
    'incidents retrieved': (r) => r.status === 200,
  });

  const duration = Date.now() - startTime;
  dbQueryTime.add(duration);
}

/**
 * Test get conversations endpoint
 */
function testGetConversations(baseUrl, sessionToken, profileId) {
  const startTime = Date.now();

  const response = http.get(`${baseUrl}/api/conversations/${profileId}`, {
    headers: { 'Authorization': `Bearer ${sessionToken}` }
  });

  check(response, {
    'conversations retrieved': (r) => r.status === 200,
  });

  const duration = Date.now() - startTime;
  dbQueryTime.add(duration);
}

/**
 * Teardown phase - runs once after all VUs complete
 */
export function teardown(data) {
  console.log('Load test completed');
  console.log('Check metrics for performance results');
}

/**
 * Handle summary reporting
 */
export function handleSummary(data) {
  const totalMessages = data.metrics.messages_processed?.values?.count || 0;
  const totalIncidents = data.metrics.incidents_detected?.values?.count || 0;
  const testDurationMinutes = (data.state?.testRunDurationMs || 0) / 60000;
  const messagesPerMinute = testDurationMinutes > 0 ? totalMessages / testDurationMinutes : 0;

  console.log('\n=== Load Test Summary ===');
  console.log(`Total messages processed: ${totalMessages}`);
  console.log(`Messages per minute: ${messagesPerMinute.toFixed(2)}`);
  console.log(`Incidents detected: ${totalIncidents}`);
  console.log(`Test duration: ${testDurationMinutes.toFixed(2)} minutes`);

  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
    'summary.json': JSON.stringify(data),
  };
}
