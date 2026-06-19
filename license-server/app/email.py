import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_code(to_email: str, code: str) -> None:
    host = os.getenv("LS_SMTP_HOST", "")
    if not host:
        logger.warning("LS_SMTP_HOST unset — not sending code to %s (dev mode)", to_email)
        return
    msg = EmailMessage()
    msg["Subject"] = "Your snflwr.ai sign-in code"
    msg["From"] = os.getenv("LS_SMTP_FROM", "noreply@snflwr.ai")
    msg["To"] = to_email
    msg.set_content(f"Your sign-in code is {code}. It expires in 10 minutes.")
    port = int(os.getenv("LS_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port) as srv:
        srv.starttls()
        user = os.getenv("LS_SMTP_USER", "")
        if user:
            srv.login(user, os.getenv("LS_SMTP_PASSWORD", ""))
        srv.send_message(msg)
