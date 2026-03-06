"""
Email Service for Parent Safety Alerts
Handles SMTP email delivery for safety notifications

[LOCKED] COPPA Compliant:
- Uses encrypted parent emails from database
- Only sends to verified parent accounts
- Includes opt-out mechanism
- Logs all email sends for audit trail
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape as html_escape
from typing import Optional, Dict, Any, List
from urllib.parse import quote as url_quote, urlparse
from datetime import datetime, timezone

from config import system_config
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from core.email_crypto import get_email_crypto
from utils.logger import get_logger, mask_email, sanitize_log_value

logger = get_logger(__name__)


def _safe_url(url: str) -> str:
    """
    Validate and sanitize a URL for safe use in HTML href attributes.

    - Rejects non-http(s) schemes (prevents javascript: protocol injection)
    - HTML-escapes the URL for safe attribute embedding
    - Returns empty string for invalid URLs

    Args:
        url: The URL to validate and escape

    Returns:
        HTML-escaped URL string safe for use in href attributes,
        or empty string if the URL is invalid/unsafe
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http'):
        return ""
    return html_escape(url, quote=True)


class EmailTemplate:
    """Email template for safety alerts"""

    @staticmethod
    def safety_alert_critical(
        parent_name: str,
        child_name: str,
        incident_count: int,
        severity: str,
        description: str,
        snippet: Optional[str] = None
    ) -> tuple[str, str]:
        """
        Generate critical safety alert email

        Returns:
            tuple: (subject, html_body)
        """
        # Escape user-controlled values to prevent stored XSS in email
        safe_parent_name = html_escape(parent_name)
        safe_child_name = html_escape(child_name)

        subject = f"[ALERT] URGENT: Safety Alert for {safe_child_name}"
        safe_severity = html_escape(severity.upper())
        safe_incident_count = html_escape(str(incident_count))
        safe_description = html_escape(description)
        safe_snippet = html_escape(snippet) if snippet else None

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #dc3545; color: white; padding: 20px; border-radius: 5px 5px 0 0; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; }}
        .alert-box {{ background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 15px 0; }}
        .snippet {{ background-color: #e9ecef; padding: 10px; margin: 10px 0; font-family: monospace; font-size: 12px; border-radius: 3px; }}
        .action {{ background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 15px 0; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #6c757d; border-radius: 0 0 5px 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>[ALERT] Urgent Safety Alert</h1>
        </div>

        <div class="content">
            <p>Dear {safe_parent_name},</p>

            <p>This is an <strong>urgent safety alert</strong> regarding your child's use of snflwr.ai.</p>

            <div class="alert-box">
                <strong>Alert Details:</strong><br>
                - Child Profile: {safe_child_name}<br>
                - Severity: {safe_severity}<br>
                - Incident Count: {safe_incident_count}<br>
                - Time: {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p')}<br>
            </div>

            <p><strong>Reason:</strong> {safe_description}</p>

            {'<div class="snippet"><strong>Conversation Excerpt:</strong><br>' + safe_snippet + '</div>' if safe_snippet else ''}

            <p><strong>[WARN] Recommended Action:</strong></p>
            <ul>
                <li>Review your child's recent activity in the parent dashboard</li>
                <li>Have a conversation with your child about safe AI use</li>
                <li>Review and adjust safety settings if needed</li>
            </ul>

            <a href="{_safe_url(system_config.BASE_URL + '/dashboard')}" class="action">View Dashboard</a>

            <p><strong>What happens next?</strong></p>
            <p>The snflwr.ai safety filter automatically blocks inappropriate content and redirects your child to educational topics. No unsafe content reaches your child. This alert is for your awareness and to help you support your child's digital safety.</p>
        </div>

        <div class="footer">
            <p>snflwr.ai - K-12 Safe Learning Platform</p>
            <p>This is an automated safety alert. Do not reply to this email.</p>
            <p><a href="{_safe_url(system_config.BASE_URL + '/preferences/email')}">Manage email preferences</a></p>
        </div>
    </div>
</body>
</html>
"""
        return subject, html

    @staticmethod
    def safety_alert_moderate(
        parent_name: str,
        child_name: str,
        incident_count: int,
        severity: str,
        description: str
    ) -> tuple[str, str]:
        """
        Generate moderate safety alert email

        Returns:
            tuple: (subject, html_body)
        """
        subject = f"[WARN] Safety Notice for {html_escape(child_name)}"

        # Escape user-controlled values to prevent stored XSS in email
        safe_parent_name = html_escape(parent_name)
        safe_child_name = html_escape(child_name)
        safe_severity = html_escape(severity.upper())
        safe_incident_count = html_escape(str(incident_count))
        safe_description = html_escape(description)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #ffc107; color: #333; padding: 20px; border-radius: 5px 5px 0 0; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; }}
        .info-box {{ background-color: #d1ecf1; border-left: 4px solid #0c5460; padding: 15px; margin: 15px 0; }}
        .action {{ background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 15px 0; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #6c757d; border-radius: 0 0 5px 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>[WARN] Safety Notice</h1>
        </div>

        <div class="content">
            <p>Hi {safe_parent_name},</p>

            <p>We wanted to inform you about some safety events involving {safe_child_name}'s use of snflwr.ai.</p>

            <div class="info-box">
                <strong>Notice Details:</strong><br>
                - Child Profile: {safe_child_name}<br>
                - Severity: {safe_severity}<br>
                - Incident Count: {safe_incident_count}<br>
                - Time: {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p')}<br>
            </div>

            <p><strong>Details:</strong> {safe_description}</p>

            <p><strong>What this means:</strong></p>
            <p>The snflwr.ai safety system detected {safe_incident_count} instance(s) of content that was redirected to educational topics. This is normal for curious children, and the system is working as intended to keep your child safe.</p>

            <a href="{_safe_url(system_config.BASE_URL + '/dashboard')}" class="action">View Full Details</a>

            <p><strong>No action required</strong> unless you notice patterns of concerning behavior. The safety filter is protecting your child automatically.</p>
        </div>

        <div class="footer">
            <p>snflwr.ai - K-12 Safe Learning Platform</p>
            <p>This is an automated safety notification. Do not reply to this email.</p>
            <p><a href="{_safe_url(system_config.BASE_URL + '/preferences/email')}">Manage email preferences</a></p>
        </div>
    </div>
</body>
</html>
"""
        return subject, html

    @staticmethod
    def email_verification(
        user_name: str,
        verification_token: str
    ) -> tuple[str, str]:
        """
        Generate email verification email

        Args:
            user_name: User's name
            verification_token: Verification token to include in URL

        Returns:
            tuple: (subject, html_body)
        """
        subject = "Verify your snflwr.ai account"

        verification_url = f"{system_config.BASE_URL}/verify-email?token={verification_token}"

        # Escape user-controlled values to prevent stored XSS in email
        safe_user_name = html_escape(user_name)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #28a745; color: white; padding: 20px; border-radius: 5px 5px 0 0; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; }}
        .info-box {{ background-color: #d1ecf1; border-left: 4px solid #0c5460; padding: 15px; margin: 15px 0; }}
        .action {{ background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 15px 0; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #6c757d; border-radius: 0 0 5px 5px; }}
        .code {{ background-color: #e9ecef; padding: 2px 6px; border-radius: 3px; font-family: monospace; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to snflwr.ai!</h1>
        </div>

        <div class="content">
            <p>Hi {safe_user_name},</p>

            <p>Thank you for creating a snflwr.ai account! To get started, please verify your email address by clicking the button below.</p>

            <a href="{verification_url}" class="action">Verify Email Address</a>

            <div class="info-box">
                <strong>[TIMER] Important:</strong> This verification link expires in <strong>24 hours</strong> for security purposes.
            </div>

            <p><strong>Why verify?</strong></p>
            <ul>
                <li>Ensures you can receive important safety alerts about your child</li>
                <li>Enables password reset functionality</li>
                <li>Complies with COPPA regulations for child safety</li>
            </ul>

            <p><strong>Didn't create an account?</strong> You can safely ignore this email.</p>

            <p>If the button doesn't work, copy and paste this link into your browser:</p>
            <p class="code">{verification_url}</p>
        </div>

        <div class="footer">
            <p>snflwr.ai - K-12 Safe Learning Platform</p>
            <p>This is an automated email. Please do not reply.</p>
        </div>
    </div>
</body>
</html>
"""
        return subject, html

    @staticmethod
    def password_reset(
        user_name: str,
        reset_token: str
    ) -> tuple[str, str]:
        """
        Generate password reset email

        Args:
            user_name: User's name
            reset_token: Password reset token to include in URL

        Returns:
            tuple: (subject, html_body)
        """
        subject = "Reset your snflwr.ai password"

        reset_url = f"{system_config.BASE_URL}/reset-password?token={reset_token}"

        # Escape user-controlled values to prevent stored XSS in email
        safe_user_name = html_escape(user_name)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #ffc107; color: #333; padding: 20px; border-radius: 5px 5px 0 0; }}
        .content {{ background-color: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; }}
        .warning-box {{ background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 15px 0; }}
        .security-box {{ background-color: #f8d7da; border-left: 4px solid #dc3545; padding: 15px; margin: 15px 0; }}
        .action {{ background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 15px 0; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #6c757d; border-radius: 0 0 5px 5px; }}
        .code {{ background-color: #e9ecef; padding: 2px 6px; border-radius: 3px; font-family: monospace; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>[LOCKED] Password Reset Request</h1>
        </div>

        <div class="content">
            <p>Hi {safe_user_name},</p>

            <p>We received a request to reset your snflwr.ai password. Click the button below to create a new password.</p>

            <a href="{reset_url}" class="action">Reset Password</a>

            <div class="warning-box">
                <strong>[TIMER] Time Sensitive:</strong> This reset link expires in <strong>1 hour</strong> for security purposes.
            </div>

            <div class="security-box">
                <strong>[LOCKED] Security Notice:</strong>
                <ul style="margin: 5px 0;">
                    <li>Your current password will remain active until you complete the reset</li>
                    <li>After reset, you'll be logged out of all devices</li>
                    <li>This link can only be used once</li>
                </ul>
            </div>

            <p><strong>Didn't request a password reset?</strong></p>
            <p>If you didn't request this, you can safely ignore this email. Your password will not be changed unless you click the link above and complete the reset process.</p>

            <p>If the button doesn't work, copy and paste this link into your browser:</p>
            <p class="code">{reset_url}</p>

            <p><strong>Need help?</strong> Contact support if you have questions about your account security.</p>
        </div>

        <div class="footer">
            <p>snflwr.ai - K-12 Safe Learning Platform</p>
            <p>This is an automated security email. Please do not reply.</p>
        </div>
    </div>
</body>
</html>
"""
        return subject, html


class EmailService:
    """
    Email service for sending parent safety alerts

    Handles SMTP connection, email sending, and audit logging
    """

    def __init__(self):
        """Initialize email service"""
        self.db = db_manager
        self.email_crypto = get_email_crypto()
        self.enabled = system_config.SMTP_ENABLED

        if self.enabled:
            logger.info(f"Email service initialized - SMTP enabled ({system_config.SMTP_HOST}:{system_config.SMTP_PORT})")
        else:
            logger.info("Email service initialized - SMTP disabled (emails will be logged only)")

    def send_safety_alert(
        self,
        parent_id: str,
        child_name: str,
        severity: str,
        incident_count: int,
        description: str,
        snippet: Optional[str] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Send safety alert email to parent

        Args:
            parent_id: Parent user ID
            child_name: Child profile name
            severity: Alert severity (critical/high/medium/low)
            incident_count: Number of incidents
            description: Alert description
            snippet: Optional conversation snippet

        Returns:
            tuple: (success, error_message or None)
        """
        try:
            # Get parent email and preferences
            parent_info = self._get_parent_email(parent_id)
            if not parent_info:
                logger.error(f"Parent not found: {parent_id}")
                return False, "Parent not found"

            parent_email, parent_name, email_enabled = parent_info

            # Check if parent has email notifications enabled
            if not email_enabled:
                logger.info(f"Email notifications disabled for parent {parent_id}")
                self._log_email_attempt(parent_id, parent_email, "skipped", "Email notifications disabled")
                return True, None  # Not an error, just skipped

            # Check if SMTP is enabled
            if not self.enabled:
                logger.warning("SMTP not configured - email not sent (logged only)")
                self._log_email_attempt(parent_id, parent_email, "not_sent", "SMTP not configured")
                return True, None  # Return success since it's expected behavior

            # Generate email template based on severity
            if severity.lower() in ['critical', 'high']:
                subject, html_body = EmailTemplate.safety_alert_critical(
                    parent_name=parent_name,
                    child_name=child_name,
                    incident_count=incident_count,
                    severity=severity,
                    description=description,
                    snippet=snippet
                )
            else:
                subject, html_body = EmailTemplate.safety_alert_moderate(
                    parent_name=parent_name,
                    child_name=child_name,
                    incident_count=incident_count,
                    severity=severity,
                    description=description
                )

            # Send email
            success, error = self._send_email(
                to_email=parent_email,
                subject=subject,
                html_body=html_body
            )

            # Log attempt
            if success:
                self._log_email_attempt(parent_id, parent_email, "sent", None)
                logger.info(f"Safety alert email sent to {mask_email(parent_email)}")
            else:
                self._log_email_attempt(parent_id, parent_email, "failed", error)
                logger.error(f"Failed to send email to {mask_email(parent_email)}: {error}")

            return success, error

        except (smtplib.SMTPException, *DB_ERRORS) as e:
            logger.exception(f"Failed to send safety alert: {e}")
            return False, str(e)

    def send_verification_email(
        self,
        user_id: str,
        user_email: str,
        user_name: str,
        verification_token: str
    ) -> tuple[bool, Optional[str]]:
        """
        Send email verification email to user

        Args:
            user_id: User ID
            user_email: User's email address (already decrypted)
            user_name: User's name
            verification_token: Verification token

        Returns:
            tuple: (success, error_message or None)
        """
        try:
            # Check if SMTP is enabled
            if not self.enabled:
                logger.warning("SMTP not configured - verification email not sent (logged only)")
                self._log_email_attempt(user_id, user_email, "not_sent", "SMTP not configured")
                return True, None  # Return success since it's expected behavior

            # Generate email template
            subject, html_body = EmailTemplate.email_verification(
                user_name=user_name,
                verification_token=verification_token
            )

            # Send email
            success, error = self._send_email(
                to_email=user_email,
                subject=subject,
                html_body=html_body
            )

            # Log attempt
            if success:
                self._log_email_attempt(user_id, user_email, "sent", None)
                logger.info(f"Verification email sent to {mask_email(user_email)}")
            else:
                self._log_email_attempt(user_id, user_email, "failed", error)
                logger.error(f"Failed to send verification email to {mask_email(user_email)}: {error}")

            return success, error

        except (smtplib.SMTPException, *DB_ERRORS) as e:
            logger.exception(f"Failed to send verification email: {e}")
            return False, str(e)

    def send_password_reset_email(
        self,
        user_id: str,
        user_email: str,
        user_name: str,
        reset_token: str
    ) -> tuple[bool, Optional[str]]:
        """
        Send password reset email to user

        Args:
            user_id: User ID
            user_email: User's email address (already decrypted)
            user_name: User's name
            reset_token: Password reset token

        Returns:
            tuple: (success, error_message or None)
        """
        try:
            # Check if SMTP is enabled
            if not self.enabled:
                logger.warning("SMTP not configured - password reset email not sent (logged only)")
                self._log_email_attempt(user_id, user_email, "not_sent", "SMTP not configured")
                return True, None  # Return success since it's expected behavior

            # Generate email template
            subject, html_body = EmailTemplate.password_reset(
                user_name=user_name,
                reset_token=reset_token
            )

            # Send email
            success, error = self._send_email(
                to_email=user_email,
                subject=subject,
                html_body=html_body
            )

            # Log attempt
            if success:
                self._log_email_attempt(user_id, user_email, "sent", None)
                logger.info("Password reset email sent")
            else:
                self._log_email_attempt(user_id, user_email, "failed", error)
                logger.error(f"Failed to send password reset email: {error}")

            return success, error

        except (smtplib.SMTPException, *DB_ERRORS) as e:
            logger.exception(f"Failed to send password reset email: {e}")
            return False, str(e)

    async def send_parental_consent_request(
        self,
        to_email: str,
        parent_name: str,
        child_name: str,
        child_age: int,
        consent_url: str
    ) -> bool:
        """
        Send parental consent verification email (COPPA compliance)

        Args:
            to_email: Parent's email address
            parent_name: Parent's name
            child_name: Child's name
            child_age: Child's age
            consent_url: URL to consent verification page

        Returns:
            True if email sent successfully
        """
        try:
            subject = f"Parental Consent Required for {child_name}'s Profile"

            # Escape user-controlled values to prevent stored XSS in email
            safe_parent_name = html_escape(parent_name)
            safe_child_name = html_escape(child_name)
            safe_child_age = html_escape(str(child_age))

            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Parental Consent Required</title>
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 28px;">[SAFE] Parental Consent Required</h1>
    </div>

    <div style="background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px;">
        <p style="font-size: 16px; margin-bottom: 20px;">
            Hello {safe_parent_name},
        </p>

        <p style="font-size: 16px; margin-bottom: 20px;">
            You're receiving this email because a profile was created for <strong>{safe_child_name}</strong> (age {safe_child_age}) in snflwr.ai.
        </p>

        <div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px; color: #856404;">
                <strong>[WARN] COPPA Compliance Notice</strong><br>
                Under the Children's Online Privacy Protection Act (COPPA), we require verifiable parental consent
                before activating profiles for children under 13 years of age.
            </p>
        </div>

        <p style="font-size: 16px; margin-bottom: 20px;">
            To activate {safe_child_name}'s profile, please verify your consent by clicking the button below:
        </p>

        <div style="text-align: center; margin: 30px 0;">
            <a href="{_safe_url(consent_url)}"
               style="display: inline-block; background: #28a745; color: white; padding: 15px 40px;
                      text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold;">
                [OK] Verify Parental Consent
            </a>
        </div>

        <p style="font-size: 14px; color: #666; margin-top: 30px;">
            <strong>What happens next:</strong>
        </p>
        <ol style="font-size: 14px; color: #666;">
            <li>Click the verification button above</li>
            <li>Review the consent form and terms of service</li>
            <li>Provide your electronic signature (type your full name)</li>
            <li>{safe_child_name}'s profile will be activated</li>
        </ol>

        <div style="background: #e9ecef; padding: 15px; margin: 20px 0; border-radius: 4px; font-size: 13px;">
            <p style="margin: 0 0 10px 0;"><strong>Privacy & Safety Features:</strong></p>
            <ul style="margin: 0; padding-left: 20px;">
                <li>All conversations monitored by 4-layer safety pipeline</li>
                <li>Age-appropriate content filtering</li>
                <li>Parent dashboard for activity monitoring</li>
                <li>Safety incident alerts</li>
                <li>Data stored locally (no cloud sharing)</li>
            </ul>
        </div>

        <p style="font-size: 13px; color: #666; margin-top: 30px;">
            This verification link expires in <strong>7 days</strong>.
        </p>

        <p style="font-size: 13px; color: #666; margin-top: 20px;">
            If you did not create this profile or do not wish to provide consent, you can safely ignore this email.
            The profile will remain inactive.
        </p>

        <hr style="border: none; border-top: 1px solid #dee2e6; margin: 30px 0;">

        <p style="font-size: 12px; color: #999; text-align: center;">
            snflwr.ai - K-12 Safe AI Learning Platform<br>
            Privacy-First - COPPA Compliant - Offline Operation
        </p>

        <p style="font-size: 11px; color: #999; text-align: center; margin-top: 20px;">
            Questions? Contact us or visit our privacy policy page.
        </p>
    </div>
</body>
</html>
"""

            # Send email
            success, error = self._send_email(
                to_email=to_email,
                subject=subject,
                html_body=html_body
            )

            if not success:
                logger.error(f"Failed to send parental consent email: {error}")
                return False

            logger.info(f"Parental consent email sent to {sanitize_log_value(mask_email(to_email))!r} for child profile")
            return True

        except smtplib.SMTPException as e:
            logger.exception(f"Failed to send parental consent request: {e}")
            return False

    def _get_parent_email(self, parent_id: str) -> Optional[tuple[str, str, bool]]:
        """
        Get parent email address from database

        Args:
            parent_id: Parent user ID

        Returns:
            tuple: (email, name, email_enabled) or None
        """
        try:
            # Get parent user record
            result = self.db.execute_query(
                """
                SELECT encrypted_email, name, email_notifications_enabled
                FROM accounts
                WHERE parent_id = ? AND role = 'parent'
                """,
                (parent_id,)
            )

            if not result:
                return None

            row = result[0]
            encrypted_email = row['encrypted_email']
            try:
                parent_name = row['name']
            except (KeyError, IndexError, TypeError):
                parent_name = 'Parent'
            try:
                email_enabled = row['email_notifications_enabled']
            except (KeyError, IndexError, TypeError):
                email_enabled = 1  # Default enabled

            # Decrypt email
            parent_email = self.email_crypto.decrypt_email(encrypted_email)

            return parent_email, parent_name, bool(email_enabled)

        except DB_ERRORS as e:
            logger.exception(f"Failed to get parent email: {e}")
            return None

    def _send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str
    ) -> tuple[bool, Optional[str]]:
        """
        Send email via SMTP

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML email body

        Returns:
            tuple: (success, error_message or None)
        """
        try:
            # Create message
            message = MIMEMultipart('alternative')
            message['Subject'] = subject
            message['From'] = f"{system_config.SMTP_FROM_NAME} <{system_config.SMTP_FROM_EMAIL}>"
            message['To'] = to_email

            # Attach HTML body
            html_part = MIMEText(html_body, 'html')
            message.attach(html_part)

            # Create SSL context
            context = ssl.create_default_context()

            # Send email
            with smtplib.SMTP(system_config.SMTP_HOST, system_config.SMTP_PORT) as server:
                if system_config.SMTP_USE_TLS:
                    server.starttls(context=context)

                # Login if credentials provided
                if system_config.SMTP_USERNAME and system_config.SMTP_PASSWORD:
                    server.login(system_config.SMTP_USERNAME, system_config.SMTP_PASSWORD)

                # Send email
                server.sendmail(
                    system_config.SMTP_FROM_EMAIL,
                    to_email,
                    message.as_string()
                )

            return True, None

        except smtplib.SMTPException as e:
            return False, str(e)

    def _log_email_attempt(
        self,
        parent_id: str,
        to_email: str,
        status: str,
        error: Optional[str]
    ):
        """
        Log email attempt to audit trail

        Args:
            parent_id: Parent user ID
            to_email: Email address
            status: Status (sent/failed/skipped/not_sent)
            error: Error message if failed
        """
        try:
            self.db.execute_write(
                """
                INSERT INTO audit_log (
                    timestamp, event_type, user_id, user_type,
                    action, details, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    'email_notification',
                    parent_id,
                    'parent',
                    'safety_alert_email',
                    f"Status: {status}, To: {mask_email(to_email)}{', Error: ' + error if error else ''}",
                    status == 'sent'
                )
            )
        except DB_ERRORS as e:
            logger.error(f"Failed to log email attempt: {e}")

    def test_connection(self) -> tuple[bool, Optional[str]]:
        """
        Test SMTP connection

        Returns:
            tuple: (success, error_message or None)
        """
        if not self.enabled:
            return False, "SMTP not enabled"

        try:
            context = ssl.create_default_context()

            with smtplib.SMTP(system_config.SMTP_HOST, system_config.SMTP_PORT, timeout=10) as server:
                if system_config.SMTP_USE_TLS:
                    server.starttls(context=context)

                if system_config.SMTP_USERNAME and system_config.SMTP_PASSWORD:
                    server.login(system_config.SMTP_USERNAME, system_config.SMTP_PASSWORD)

            logger.info("SMTP connection test successful")
            return True, None

        except smtplib.SMTPException as e:
            error_msg = str(e)
            logger.error(f"SMTP connection test failed: {error_msg}")
            return False, error_msg


# Global email service instance
email_service = EmailService()


__all__ = ['EmailService', 'EmailTemplate', 'email_service']
