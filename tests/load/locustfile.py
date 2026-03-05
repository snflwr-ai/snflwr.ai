"""
snflwr.ai Load Testing Suite (Locust)

Alternative Python-based load testing using Locust.
Tests system performance under production load conditions.

Usage:
    # Web UI mode (recommended)
    locust -f tests/load/locustfile.py --host=http://localhost:8000

    # Headless mode
    locust -f tests/load/locustfile.py --host=http://localhost:8000 \
           --users 100 --spawn-rate 10 --run-time 10m --headless

    # Distributed mode (master)
    locust -f tests/load/locustfile.py --master

    # Distributed mode (worker)
    locust -f tests/load/locustfile.py --worker --master-host=localhost

Installation:
    pip install locust
"""

from locust import HttpUser, task, between, events
from locust.runners import MasterRunner
import os
import random
import json
import time
import logging

logger = logging.getLogger(__name__)

# Test data configuration
PARENT_IDS = [f"load-test-parent-{i}" for i in range(20)]
PROFILE_IDS = [f"load-test-profile-{i}" for i in range(50)]

TEST_MESSAGES = [
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
]

# Custom metrics
message_count = 0
incident_count = 0
safety_check_times = []


class SnflwrUser(HttpUser):
    """
    Simulated snflwr.ai parent user.

    Behavior:
    1. Authenticates to get session token
    2. Sends chat messages through safety pipeline
    3. Checks profile information
    4. Reviews safety incidents
    5. Retrieves conversation history
    """

    # Wait 1-5 seconds between tasks (realistic human behavior)
    wait_time = between(1, 5)

    def on_start(self):
        """Called when a user starts. Performs authentication."""
        self.parent_id = random.choice(PARENT_IDS)
        self.profile_id = random.choice(PROFILE_IDS)
        self.session_token = None

        # Authenticate
        self.authenticate()

    def authenticate(self):
        """Authenticate and obtain session token."""
        with self.client.post(
            "/api/auth/login",
            json={
                "username": self.parent_id,
                "password": "load-test-password"
            },
            catch_response=True,
            name="Auth: Login"
        ) as response:
            if response.status_code in [200, 201]:
                try:
                    data = response.json()
                    self.session_token = data.get('session_token', 'mock-token')
                    response.success()
                except Exception as e:
                    logger.error(f"Failed to parse auth response: {e}")
                    self.session_token = 'mock-token'
                    response.failure(f"Parse error: {e}")
            else:
                response.failure(f"Authentication failed: {response.status_code}")
                self.session_token = 'mock-token'  # Fallback for testing

    @task(10)
    def send_chat_message(self):
        """
        Send chat message through safety pipeline.
        Highest weight (10) - most frequent operation.
        """
        global message_count, incident_count, safety_check_times

        message = random.choice(TEST_MESSAGES)

        start_time = time.time()

        with self.client.post(
            "/api/chat",
            json={
                "profile_id": self.profile_id,
                "message": message,
                "model": os.environ.get("OLLAMA_DEFAULT_MODEL", ""),
                "conversation_id": f"conv-{self.profile_id}-{int(time.time())}"
            },
            headers={"Authorization": f"Bearer {self.session_token}"},
            catch_response=True,
            name="Chat: Send Message"
        ) as response:
            duration = (time.time() - start_time) * 1000  # Convert to ms

            if response.status_code == 200:
                try:
                    data = response.json()

                    # Track message processing
                    message_count += 1

                    # Track safety incidents
                    if data.get('safety_incident'):
                        incident_count += 1

                    # Track safety check duration
                    if 'safety_check_duration' in data:
                        safety_check_times.append(data['safety_check_duration'])

                    # Verify response quality
                    if not data.get('response') or len(data['response']) == 0:
                        response.failure("Empty response received")
                    else:
                        response.success()

                except Exception as e:
                    response.failure(f"Invalid JSON response: {e}")
            else:
                response.failure(f"Chat failed: {response.status_code}")

    @task(3)
    def get_profile(self):
        """
        Get child profile information.
        Medium weight (3) - moderate frequency.
        """
        with self.client.get(
            f"/api/profiles/{self.profile_id}",
            headers={"Authorization": f"Bearer {self.session_token}"},
            catch_response=True,
            name="Profile: Get Info"
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Failed to get profile: {response.status_code}")

    @task(2)
    def get_incidents(self):
        """
        Get safety incidents for profile.
        Lower weight (2) - less frequent.
        """
        with self.client.get(
            f"/api/safety/incidents?profile_id={self.profile_id}",
            headers={"Authorization": f"Bearer {self.session_token}"},
            catch_response=True,
            name="Safety: Get Incidents"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed to get incidents: {response.status_code}")

    @task(2)
    def get_conversations(self):
        """
        Get conversation history.
        Lower weight (2) - less frequent.
        """
        with self.client.get(
            f"/api/conversations/{self.profile_id}",
            headers={"Authorization": f"Bearer {self.session_token}"},
            catch_response=True,
            name="Conversations: Get History"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed to get conversations: {response.status_code}")

    @task(1)
    def get_profile_stats(self):
        """
        Get profile statistics.
        Lowest weight (1) - least frequent.
        """
        with self.client.get(
            f"/api/profiles/{self.profile_id}/stats",
            headers={"Authorization": f"Bearer {self.session_token}"},
            catch_response=True,
            name="Profile: Get Stats"
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Failed to get stats: {response.status_code}")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when load test starts."""
    global message_count, incident_count, safety_check_times

    message_count = 0
    incident_count = 0
    safety_check_times = []

    print("\n" + "="*60)
    print("snflwr.ai Load Test Starting")
    print("="*60)
    print(f"Target: {environment.host}")
    print(f"Expected: 100 concurrent users, 1000+ messages/minute")
    print("="*60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when load test stops. Print summary."""
    global message_count, incident_count, safety_check_times

    # Calculate test duration
    if hasattr(environment.runner, 'stats'):
        stats = environment.runner.stats
        total_requests = stats.total.num_requests
        failed_requests = stats.total.num_failures
        failure_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0

        # Calculate messages per minute
        test_duration_seconds = (
            environment.runner.stats.last_request_timestamp -
            environment.runner.stats.start_time
        )
        test_duration_minutes = test_duration_seconds / 60 if test_duration_seconds > 0 else 1
        messages_per_minute = message_count / test_duration_minutes

        # Calculate average safety check time
        avg_safety_check_time = (
            sum(safety_check_times) / len(safety_check_times)
            if safety_check_times else 0
        )

        print("\n" + "="*60)
        print("Load Test Summary")
        print("="*60)
        print(f"Total HTTP Requests:     {total_requests:,}")
        print(f"Failed Requests:         {failed_requests:,} ({failure_rate:.2f}%)")
        print(f"Messages Processed:      {message_count:,}")
        print(f"Messages/Minute:         {messages_per_minute:.2f}")
        print(f"Incidents Detected:      {incident_count:,}")
        print(f"Avg Safety Check Time:   {avg_safety_check_time:.2f}ms")
        print(f"Test Duration:           {test_duration_minutes:.2f} minutes")
        print("="*60)

        # Performance evaluation
        print("\nPerformance Evaluation:")
        print("-" * 60)

        # Check thresholds
        checks = []

        if messages_per_minute >= 1000:
            checks.append(("[OK]", f"Messages/min ≥ 1000 ({messages_per_minute:.2f})"))
        else:
            checks.append(("[FAIL]", f"Messages/min < 1000 ({messages_per_minute:.2f})"))

        if failure_rate < 5:
            checks.append(("[OK]", f"Failure rate < 5% ({failure_rate:.2f}%)"))
        else:
            checks.append(("[FAIL]", f"Failure rate ≥ 5% ({failure_rate:.2f}%)"))

        if avg_safety_check_time < 1000:
            checks.append(("[OK]", f"Safety checks < 1s ({avg_safety_check_time:.2f}ms)"))
        else:
            checks.append(("[FAIL]", f"Safety checks ≥ 1s ({avg_safety_check_time:.2f}ms)"))

        for symbol, msg in checks:
            print(f"{symbol} {msg}")

        # Overall result
        all_passed = all(symbol == "[OK]" for symbol, _ in checks)
        print("\n" + "="*60)
        if all_passed:
            print("[OK] LOAD TEST PASSED - System meets production requirements")
        else:
            print("[FAIL] LOAD TEST FAILED - Performance issues detected")
        print("="*60 + "\n")


@events.init_command_line_parser.add_listener
def on_init_command_line_parser(parser):
    """Add custom command line arguments."""
    parser.add_argument(
        "--target-messages-per-minute",
        type=int,
        default=1000,
        help="Target messages per minute threshold (default: 1000)"
    )
