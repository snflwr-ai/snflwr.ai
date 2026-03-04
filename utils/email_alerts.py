# utils/email_alerts.py
"""
SMTP Email Alert System for Parent Notifications
Production-ready email system for safety incidents and system alerts
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import List, Optional, Dict
from datetime import datetime, timezone
from pathlib import Path
import threading
from queue import Queue
import time

from config import system_config
from storage.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)


class EmailConfig:
    """Email configuration settings"""

    # SMTP Configuration (defaults for common providers)
    SMTP_HOST = "smtp.gmail.com"  # Change to your SMTP server
    SMTP_PORT = 587  # 587 for TLS, 465 for SSL
    SMTP_USE_TLS = True
    SMTP_USE_SSL = False

    # Authentication
    SMTP_USERNAME = ""  # Set via environment variable or config
    SMTP_PASSWORD = ""  # Set via environment variable or config

    # Sender information
    FROM_EMAIL = "noreply@snflwr.ai"
    FROM_NAME = "snflwr.ai Safety Team"

    # Email settings
    ENABLE_EMAIL_ALERTS = False  # Must be explicitly enabled
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds

    # Queue settings
    BATCH_SIZE = 10  # Send emails in batches
    BATCH_DELAY = 1  # seconds between batches

    @classmethod
    def load_from_env(cls):
        """Load configuration from environment variables"""
        import os

        cls.SMTP_HOST = os.getenv('SMTP_HOST', cls.SMTP_HOST)
        cls.SMTP_PORT = int(os.getenv('SMTP_PORT', cls.SMTP_PORT))
        cls.SMTP_USERNAME = os.getenv('SMTP_USERNAME', cls.SMTP_USERNAME)
        cls.SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', cls.SMTP_PASSWORD)
        cls.FROM_EMAIL = os.getenv('FROM_EMAIL', cls.FROM_EMAIL)
        cls.FROM_NAME = os.getenv('FROM_NAME', cls.FROM_NAME)
        cls.ENABLE_EMAIL_ALERTS = os.getenv('ENABLE_EMAIL_ALERTS', 'false').lower() == 'true'


class EmailTemplate:
    """Email templates for different alert types"""

    @staticmethod
    def safety_incident_critical(child_name: str, incident_type: str, incident_id: int, timestamp: str) -> tuple:
        """Critical safety incident template"""
        subject = f"🚨 URGENT: Critical Safety Alert for {child_name}"

        body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #dc3545; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
            <h2 style="margin: 0;">🚨 URGENT Safety Alert</h2>
        </div>

        <p>Dear Parent/Guardian,</p>

        <p><strong>We detected a critical safety concern in {child_name}'s conversation.</strong></p>

        <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #dc3545; margin: 20px 0;">
            <p style="margin: 0;"><strong>Incident Details:</strong></p>
            <ul style="margin: 10px 0;">
                <li><strong>Type:</strong> {incident_type}</li>
                <li><strong>Time:</strong> {timestamp}</li>
                <li><strong>Incident ID:</strong> #{incident_id}</li>
            </ul>
        </div>

        <p><strong>Immediate Action Recommended:</strong></p>
        <ul>
            <li>Review the full incident details in your parent dashboard</li>
            <li>Have a conversation with {child_name} about online safety</li>
            <li>Contact your child's school counselor if needed</li>
        </ul>

        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <p style="margin: 0;"><strong>📞 Need Help?</strong></p>
            <p style="margin: 5px 0;">
                If you're concerned about your child's safety, please contact:
            </p>
            <ul style="margin: 5px 0;">
                <li>National Suicide Prevention Lifeline: 988</li>
                <li>Crisis Text Line: Text HOME to 741741</li>
            </ul>
        </div>

        <p>
            <a href="#" style="display: inline-block; background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px;">
                View Full Incident Report
            </a>
        </p>

        <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">

        <p style="font-size: 12px; color: #666;">
            This is an automated safety alert from snflwr.ai.
            All conversations are monitored for child safety.
        </p>
    </div>
</body>
</html>
        """

        return subject, body

    @staticmethod
    def safety_incident_major(child_name: str, incident_count: int, incident_types: List[str], period_days: int) -> tuple:
        """Major safety incident template"""
        subject = f"⚠️ Important Safety Alert for {child_name}"

        types_list = ', '.join(incident_types[:3])  # Show up to 3 types

        body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #ffc107; color: #000; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
            <h2 style="margin: 0;">⚠️ Important Safety Alert</h2>
        </div>

        <p>Dear Parent/Guardian,</p>

        <p>We've detected multiple safety concerns in {child_name}'s recent conversations.</p>

        <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0;">
            <p style="margin: 0;"><strong>Summary:</strong></p>
            <ul style="margin: 10px 0;">
                <li><strong>{incident_count}</strong> incidents in the past {period_days} days</li>
                <li><strong>Categories:</strong> {types_list}</li>
            </ul>
        </div>

        <p><strong>Recommended Actions:</strong></p>
        <ul>
            <li>Review all incidents in your parent dashboard</li>
            <li>Discuss appropriate online behavior with {child_name}</li>
            <li>Monitor upcoming conversations more closely</li>
        </ul>

        <p>
            <a href="#" style="display: inline-block; background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px;">
                Review Incident History
            </a>
        </p>

        <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">

        <p style="font-size: 12px; color: #666;">
            This is an automated safety alert from snflwr.ai.
        </p>
    </div>
</body>
</html>
        """

        return subject, body

    @staticmethod
    def daily_digest(parent_name: str, summary_data: Dict) -> tuple:
        """Daily activity digest template"""
        subject = "📊 Daily Activity Summary - snflwr.ai"

        total_sessions = summary_data.get('total_sessions', 0)
        total_questions = summary_data.get('total_questions', 0)
        incidents = summary_data.get('incidents', 0)

        body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #28a745; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
            <h2 style="margin: 0;">📊 Daily Activity Summary</h2>
        </div>

        <p>Hello {parent_name},</p>

        <p>Here's your daily summary of your child's learning activity:</p>

        <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
            <table style="width: 100%;">
                <tr>
                    <td style="padding: 10px;"><strong>Learning Sessions:</strong></td>
                    <td style="text-align: right;">{total_sessions}</td>
                </tr>
                <tr>
                    <td style="padding: 10px;"><strong>Questions Asked:</strong></td>
                    <td style="text-align: right;">{total_questions}</td>
                </tr>
                <tr>
                    <td style="padding: 10px;"><strong>Safety Incidents:</strong></td>
                    <td style="text-align: right;">{incidents}</td>
                </tr>
            </table>
        </div>

        <p>
            <a href="#" style="display: inline-block; background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px;">
                View Detailed Report
            </a>
        </p>

        <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">

        <p style="font-size: 12px; color: #666;">
            snflwr.ai - Safe Learning for Every Child
        </p>
    </div>
</body>
</html>
        """

        return subject, body

    @staticmethod
    def system_error_alert(error_summary: str, error_count: int) -> tuple:
        """System error alert for administrators"""
        subject = f"🔧 System Alert: {error_count} errors detected"

        body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #dc3545; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
            <h2 style="margin: 0;">🔧 System Error Alert</h2>
        </div>

        <p>System Administrator,</p>

        <p><strong>{error_count}</strong> errors have been detected in the production system.</p>

        <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #dc3545; margin: 20px 0;">
            <p style="margin: 0;"><strong>Summary:</strong></p>
            <pre style="margin: 10px 0; font-size: 12px;">{error_summary}</pre>
        </div>

        <p><strong>Action Required:</strong></p>
        <ul>
            <li>Review error logs immediately</li>
            <li>Check system health dashboard</li>
            <li>Investigate root cause</li>
        </ul>

        <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">

        <p style="font-size: 12px; color: #666;">
            Automated system alert - snflwr.ai
        </p>
    </div>
</body>
</html>
        """

        return subject, body


class EmailAlertSystem:
    """
    Production email alert system with queuing and retry logic
    """

    def __init__(self):
        """Initialize email alert system"""
        self.config = EmailConfig()
        self.config.load_from_env()
        self.db = db_manager

        # Email queue
        self.email_queue: Queue = Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False

        logger.info(f"Email Alert System initialized (enabled: {self.config.ENABLE_EMAIL_ALERTS})")

    def start_worker(self):
        """Start background email worker thread"""
        if self.running:
            logger.warning("Email worker already running")
            return

        self.running = True
        self.worker_thread = threading.Thread(
            target=self._process_queue,
            daemon=True,
            name="EmailWorker"
        )
        self.worker_thread.start()

        logger.info("Email worker thread started")

    def stop_worker(self):
        """Stop background email worker"""
        if not self.running:
            return

        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)

        logger.info("Email worker thread stopped")

    def _process_queue(self):
        """Process email queue in background"""
        while self.running:
            try:
                if not self.email_queue.empty():
                    email_data = self.email_queue.get(timeout=1)
                    self._send_email_with_retry(email_data)
                else:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Email queue processing error: {e}")

    def send_safety_alert(
        self,
        parent_email: str,
        child_name: str,
        incident_type: str,
        severity: str,
        incident_id: int,
        timestamp: Optional[str] = None
    ):
        """
        Send safety incident alert to parent

        Args:
            parent_email: Parent's email address
            child_name: Child's name
            incident_type: Type of incident
            severity: 'critical', 'major', 'minor'
            incident_id: Incident ID
            timestamp: When incident occurred
        """
        if not self.config.ENABLE_EMAIL_ALERTS:
            logger.info("Email alerts disabled, skipping send")
            return

        timestamp = timestamp or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        # Generate appropriate template
        if severity == 'critical':
            subject, body = EmailTemplate.safety_incident_critical(
                child_name, incident_type, incident_id, timestamp
            )
        else:
            # For major/minor, we'll use the major template with count=1
            subject, body = EmailTemplate.safety_incident_major(
                child_name, 1, [incident_type], 1
            )

        # Queue email
        email_data = {
            'to_email': parent_email,
            'subject': subject,
            'body': body,
            'is_html': True,
            'priority': 'high' if severity == 'critical' else 'normal'
        }

        self.email_queue.put(email_data)
        logger.info(f"Safety alert queued for {parent_email} (incident #{incident_id})")

    def send_daily_digest(self, parent_email: str, parent_name: str, summary_data: Dict):
        """Send daily activity digest"""
        if not self.config.ENABLE_EMAIL_ALERTS:
            return

        subject, body = EmailTemplate.daily_digest(parent_name, summary_data)

        email_data = {
            'to_email': parent_email,
            'subject': subject,
            'body': body,
            'is_html': True,
            'priority': 'low'
        }

        self.email_queue.put(email_data)
        logger.info(f"Daily digest queued for {parent_email}")

    def send_error_alert(self, admin_email: str, error_summary: str, error_count: int):
        """Send system error alert to administrator"""
        if not self.config.ENABLE_EMAIL_ALERTS:
            return

        subject, body = EmailTemplate.system_error_alert(error_summary, error_count)

        email_data = {
            'to_email': admin_email,
            'subject': subject,
            'body': body,
            'is_html': True,
            'priority': 'high'
        }

        self.email_queue.put(email_data)
        logger.info(f"Error alert queued for {admin_email}")

    def _send_email_with_retry(self, email_data: Dict) -> bool:
        """Send email with retry logic"""
        for attempt in range(self.config.MAX_RETRIES):
            try:
                success = self._send_email(
                    to_email=email_data['to_email'],
                    subject=email_data['subject'],
                    body=email_data['body'],
                    is_html=email_data.get('is_html', True)
                )

                if success:
                    logger.info(f"Email sent successfully to {email_data['to_email']}")
                    return True

            except (smtplib.SMTPException, ConnectionError, OSError) as e:
                logger.error(f"Email send attempt {attempt + 1} failed: {e}")

                if attempt < self.config.MAX_RETRIES - 1:
                    time.sleep(self.config.RETRY_DELAY)

        logger.error(f"Failed to send email to {email_data['to_email']} after {self.config.MAX_RETRIES} attempts")
        return False

    def _send_email(self, to_email: str, subject: str, body: str, is_html: bool = True) -> bool:
        """
        Send individual email via SMTP

        Args:
            to_email: Recipient email
            subject: Email subject
            body: Email body
            is_html: Whether body is HTML

        Returns:
            True if successful
        """
        try:
            # Create message
            message = MIMEMultipart('alternative')
            message['From'] = formataddr((self.config.FROM_NAME, self.config.FROM_EMAIL))
            message['To'] = to_email
            message['Subject'] = subject

            # Add body
            if is_html:
                message.attach(MIMEText(body, 'html'))
            else:
                message.attach(MIMEText(body, 'plain'))

            # Create secure connection and send
            if self.config.SMTP_USE_SSL:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.config.SMTP_HOST, self.config.SMTP_PORT, context=context) as server:
                    if self.config.SMTP_USERNAME and self.config.SMTP_PASSWORD:
                        server.login(self.config.SMTP_USERNAME, self.config.SMTP_PASSWORD)
                    server.sendmail(self.config.FROM_EMAIL, to_email, message.as_string())
            else:
                with smtplib.SMTP(self.config.SMTP_HOST, self.config.SMTP_PORT) as server:
                    if self.config.SMTP_USE_TLS:
                        server.starttls()
                    if self.config.SMTP_USERNAME and self.config.SMTP_PASSWORD:
                        server.login(self.config.SMTP_USERNAME, self.config.SMTP_PASSWORD)
                    server.sendmail(self.config.FROM_EMAIL, to_email, message.as_string())

            return True

        except (smtplib.SMTPException, ConnectionError, OSError) as e:
            logger.error(f"SMTP error: {e}")
            raise

    def test_connection(self) -> bool:
        """Test SMTP connection"""
        try:
            if self.config.SMTP_USE_SSL:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.config.SMTP_HOST, self.config.SMTP_PORT, context=context) as server:
                    if self.config.SMTP_USERNAME and self.config.SMTP_PASSWORD:
                        server.login(self.config.SMTP_USERNAME, self.config.SMTP_PASSWORD)
            else:
                with smtplib.SMTP(self.config.SMTP_HOST, self.config.SMTP_PORT) as server:
                    if self.config.SMTP_USE_TLS:
                        server.starttls()
                    if self.config.SMTP_USERNAME and self.config.SMTP_PASSWORD:
                        server.login(self.config.SMTP_USERNAME, self.config.SMTP_PASSWORD)

            logger.info("SMTP connection test successful")
            return True

        except (smtplib.SMTPException, ConnectionError, OSError) as e:
            logger.error(f"SMTP connection test failed: {e}")
            return False


# Singleton instance
email_alert_system = EmailAlertSystem()


# Export public interface
__all__ = [
    'EmailAlertSystem',
    'EmailConfig',
    'EmailTemplate',
    'email_alert_system'
]
