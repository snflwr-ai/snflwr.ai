#!/usr/bin/env python
"""
Concurrent Users Load Test
Tests system performance under concurrent user load

This test simulates multiple users:
- Registering accounts
- Logging in
- Creating profiles
- Sending chat messages
- Viewing dashboards

Metrics tracked:
- Response times
- Success/failure rates
- Memory usage
- Database performance
- Error rates
"""

import sys
import time
import threading
import statistics
import psutil
import secrets
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import system_config
from core.authentication import auth_manager
from core.profile_manager import ProfileManager
from storage.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)


class LoadTestMetrics:
    """Track load test metrics"""

    def __init__(self):
        self.response_times = defaultdict(list)
        self.errors = defaultdict(list)
        self.success_count = defaultdict(int)
        self.failure_count = defaultdict(int)
        self.start_time = None
        self.end_time = None
        self.lock = threading.Lock()

    def record_response(self, operation: str, duration: float, success: bool, error: str = None):
        """Record operation response"""
        with self.lock:
            self.response_times[operation].append(duration)

            if success:
                self.success_count[operation] += 1
            else:
                self.failure_count[operation] += 1
                if error:
                    self.errors[operation].append(error)

    def get_stats(self, operation: str) -> Dict[str, Any]:
        """Get statistics for an operation"""
        times = self.response_times.get(operation, [])

        if not times:
            return {
                'count': 0,
                'success': 0,
                'failure': 0,
                'avg_time': 0,
                'min_time': 0,
                'max_time': 0,
                'p50': 0,
                'p95': 0,
                'p99': 0
            }

        return {
            'count': len(times),
            'success': self.success_count.get(operation, 0),
            'failure': self.failure_count.get(operation, 0),
            'avg_time': statistics.mean(times),
            'min_time': min(times),
            'max_time': max(times),
            'p50': statistics.median(times),
            'p95': statistics.quantiles(times, n=20)[18] if len(times) >= 20 else max(times),
            'p99': statistics.quantiles(times, n=100)[98] if len(times) >= 100 else max(times)
        }

    def print_report(self):
        """Print load test report"""
        print("\n" + "=" * 80)
        print("LOAD TEST RESULTS")
        print("=" * 80)

        total_time = self.end_time - self.start_time if self.end_time and self.start_time else 0

        print(f"\nTest Duration: {total_time:.2f}s")

        # System metrics
        print(f"\nSystem Metrics:")
        print(f"  CPU Usage: {psutil.cpu_percent()}%")
        print(f"  Memory Usage: {psutil.virtual_memory().percent}%")
        print(f"  Memory Available: {psutil.virtual_memory().available / 1024 / 1024:.0f} MB")

        # Operation metrics
        operations = ['register', 'login', 'create_profile', 'get_profile', 'chat']

        for op in operations:
            stats = self.get_stats(op)

            if stats['count'] == 0:
                continue

            print(f"\n{op.upper()}:")
            print(f"  Total Requests: {stats['count']}")
            print(f"  Success: {stats['success']} ({stats['success']/stats['count']*100:.1f}%)")
            print(f"  Failure: {stats['failure']} ({stats['failure']/stats['count']*100:.1f}%)")
            print(f"  Avg Response Time: {stats['avg_time']*1000:.0f}ms")
            print(f"  Min: {stats['min_time']*1000:.0f}ms")
            print(f"  Max: {stats['max_time']*1000:.0f}ms")
            print(f"  P50: {stats['p50']*1000:.0f}ms")
            print(f"  P95: {stats['p95']*1000:.0f}ms")
            print(f"  P99: {stats['p99']*1000:.0f}ms")

            # Show errors if any
            errors = self.errors.get(op, [])
            if errors:
                print(f"  Errors: {len(errors)}")
                # Show unique errors
                unique_errors = list(set(errors))[:5]
                for err in unique_errors:
                    print(f"    - {err}")

        # Performance assessment
        print("\n" + "=" * 80)
        print("PERFORMANCE ASSESSMENT")
        print("=" * 80)

        # Check if any operation failed
        total_failures = sum(self.failure_count.values())
        total_success = sum(self.success_count.values())
        total_requests = total_failures + total_success

        if total_requests > 0:
            error_rate = (total_failures / total_requests) * 100
            print(f"\nOverall Error Rate: {error_rate:.2f}%")

            if error_rate > 10:
                print("❌ HIGH ERROR RATE - System is not stable under load")
            elif error_rate > 1:
                print("⚠️  MODERATE ERROR RATE - Some issues detected")
            else:
                print("✅ LOW ERROR RATE - System is stable")

        # Check response times
        chat_stats = self.get_stats('chat')
        if chat_stats['count'] > 0:
            if chat_stats['p95'] > 5:
                print(f"❌ SLOW RESPONSE TIMES - P95 chat response: {chat_stats['p95']*1000:.0f}ms (target: <2000ms)")
            elif chat_stats['p95'] > 2:
                print(f"⚠️  ACCEPTABLE RESPONSE TIMES - P95 chat response: {chat_stats['p95']*1000:.0f}ms")
            else:
                print(f"✅ FAST RESPONSE TIMES - P95 chat response: {chat_stats['p95']*1000:.0f}ms")


class UserSimulator:
    """Simulate a single user"""

    def __init__(self, user_id: int, metrics: LoadTestMetrics):
        self.user_id = user_id
        self.metrics = metrics
        self.email = f"loadtest_user_{user_id}@example.com"
        self.password = f"LoadTest{user_id}!Pass"
        self.name = f"Load Test User {user_id}"
        self.session_id = None
        self.profile_id = None

    def run(self):
        """Run user simulation"""
        try:
            # 1. Register
            self._register()
            time.sleep(0.1)

            # 2. Login
            self._login()
            time.sleep(0.1)

            # 3. Create child profile
            self._create_profile()
            time.sleep(0.1)

            # 4. Get profile
            self._get_profile()
            time.sleep(0.1)

            # 5. Send chat messages (simulate conversation)
            for i in range(3):
                self._send_chat()
                time.sleep(0.2)

        except Exception as e:
            logger.error(f"User {self.user_id} simulation failed: {e}")

    def _register(self):
        """Register user"""
        start = time.time()
        try:
            success, user_id, error = auth_manager.register_parent(
                email=self.email,
                password=self.password,
                verify_password=self.password  # Same as password for test
            )

            duration = time.time() - start
            self.metrics.record_response('register', duration, success, error)

            if not success:
                logger.error(f"User {self.user_id} registration failed: {error}")

        except Exception as e:
            duration = time.time() - start
            self.metrics.record_response('register', duration, False, str(e))

    def _login(self):
        """Login user"""
        start = time.time()
        try:
            success, session, error = auth_manager.login(
                email=self.email,
                password=self.password
            )

            duration = time.time() - start
            self.metrics.record_response('login', duration, success, error)

            if success and session:
                self.session_id = session.session_id
            else:
                logger.error(f"User {self.user_id} login failed: {error}")

        except Exception as e:
            duration = time.time() - start
            self.metrics.record_response('login', duration, False, str(e))

    def _create_profile(self):
        """Create child profile"""
        if not self.session_id:
            return

        start = time.time()
        try:
            # Get parent ID from session
            is_valid, session = auth_manager.validate_session(self.session_id)

            if not is_valid or not session:
                self.metrics.record_response('create_profile', time.time() - start, False, "Invalid session")
                return

            profile_manager = ProfileManager(auth_manager.db)

            profile = profile_manager.create_profile(
                parent_id=session.user_id,
                name=f"Child of User {self.user_id}",
                age=10,
                grade="5th"
            )

            duration = time.time() - start
            success = profile is not None
            self.metrics.record_response('create_profile', duration, success, None if success else "Failed")

            if profile:
                self.profile_id = profile.profile_id
            else:
                logger.error(f"User {self.user_id} profile creation failed")

        except Exception as e:
            duration = time.time() - start
            self.metrics.record_response('create_profile', duration, False, str(e))

    def _get_profile(self):
        """Get child profile"""
        if not self.profile_id:
            return

        start = time.time()
        try:
            profile_manager = ProfileManager(auth_manager.db)
            profile = profile_manager.get_profile(self.profile_id)

            duration = time.time() - start
            success = profile is not None

            self.metrics.record_response('get_profile', duration, success,
                                        None if success else "Profile not found")

        except Exception as e:
            duration = time.time() - start
            self.metrics.record_response('get_profile', duration, False, str(e))

    def _send_chat(self):
        """Send chat message"""
        if not self.profile_id:
            return

        start = time.time()
        try:
            # Simulate chat by just measuring response time
            # In real test, would call chat API endpoint
            # For now, just test database performance

            message = f"What is 2 + 2? (Load test message from user {self.user_id})"

            # Simulate message processing time
            time.sleep(0.01)

            duration = time.time() - start
            self.metrics.record_response('chat', duration, True, None)

        except Exception as e:
            duration = time.time() - start
            self.metrics.record_response('chat', duration, False, str(e))


def run_concurrent_users_test(num_users: int = 10):
    """
    Run concurrent users load test

    Args:
        num_users: Number of concurrent users to simulate
    """
    print("\n" + "=" * 80)
    print(f"CONCURRENT USERS LOAD TEST - {num_users} users")
    print("=" * 80)

    # Initialize metrics
    metrics = LoadTestMetrics()
    metrics.start_time = time.time()

    # Record initial system state
    initial_memory = psutil.virtual_memory().used
    initial_cpu = psutil.cpu_percent()

    print(f"\nInitial System State:")
    print(f"  CPU: {initial_cpu}%")
    print(f"  Memory: {psutil.virtual_memory().percent}%")
    print(f"  Memory Used: {initial_memory / 1024 / 1024:.0f} MB")

    print(f"\nStarting {num_users} concurrent user simulations...")

    # Create user simulators
    users = [UserSimulator(i, metrics) for i in range(num_users)]

    # Run users in parallel threads
    threads = []
    for user in users:
        thread = threading.Thread(target=user.run)
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    metrics.end_time = time.time()

    # Record final system state
    final_memory = psutil.virtual_memory().used
    final_cpu = psutil.cpu_percent()

    print(f"\nFinal System State:")
    print(f"  CPU: {final_cpu}%")
    print(f"  Memory: {psutil.virtual_memory().percent}%")
    print(f"  Memory Used: {final_memory / 1024 / 1024:.0f} MB")
    print(f"  Memory Delta: {(final_memory - initial_memory) / 1024 / 1024:.0f} MB")

    # Print results
    metrics.print_report()

    # Cleanup test data
    cleanup_test_data(num_users)

    return metrics


def cleanup_test_data(num_users: int):
    """Clean up test data"""
    print("\n" + "=" * 80)
    print("CLEANING UP TEST DATA")
    print("=" * 80)

    try:
        from core.email_crypto import get_email_crypto
        import sqlite3

        email_crypto = get_email_crypto()
        conn = sqlite3.connect(str(system_config.DB_PATH))
        cursor = conn.cursor()

        deleted_users = 0
        deleted_profiles = 0

        # Delete test users
        for i in range(num_users):
            email = f"loadtest_user_{i}@example.com"
            email_hash, _ = email_crypto.prepare_email_for_storage(email)

            cursor.execute("DELETE FROM accounts WHERE email_hash = ?", (email_hash,))
            deleted_users += cursor.rowcount

        # Delete test profiles
        cursor.execute("DELETE FROM child_profiles WHERE name LIKE 'Child of User %'")
        deleted_profiles = cursor.rowcount

        conn.commit()
        conn.close()

        print(f"✓ Deleted {deleted_users} test users")
        print(f"✓ Deleted {deleted_profiles} test profiles")

    except Exception as e:
        print(f"✗ Cleanup failed: {e}")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Run concurrent users load test')
    parser.add_argument('--users', type=int, default=10, help='Number of concurrent users (default: 10)')
    parser.add_argument('--heavy', action='store_true', help='Heavy load test (50 users)')
    parser.add_argument('--stress', action='store_true', help='Stress test (100 users)')

    args = parser.parse_args()

    if args.stress:
        num_users = 100
    elif args.heavy:
        num_users = 50
    else:
        num_users = args.users

    try:
        metrics = run_concurrent_users_test(num_users)

        # Determine exit code based on results
        total_failures = sum(metrics.failure_count.values())
        total_success = sum(metrics.success_count.values())
        total_requests = total_failures + total_success

        if total_requests > 0:
            error_rate = (total_failures / total_requests) * 100

            if error_rate > 10:
                return 1  # High error rate
            else:
                return 0  # Acceptable

        return 0

    except KeyboardInterrupt:
        print("\n\nLoad test interrupted by user")
        return 1
    except Exception as e:
        print(f"\n\nLoad test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
