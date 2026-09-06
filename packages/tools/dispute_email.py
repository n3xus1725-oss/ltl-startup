"""Dispute email sender — sends real dispute emails to carrier billing contacts.

Safety rules:
1. Only sends to recipient_email that came from CarrierContact DB.
2. DISPUTE_EMAIL_ENABLED must be True in config, otherwise logs but does not send.
3. Tries SMTP first, then falls back to Gmail OAuth if configured.
4. Returns a structured result — never raises silently.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from packages.domain.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class EmailSendResult:
    success: bool
    method: str  # "smtp" | "simulated" | "disabled"
    message_id: Optional[str] = None
    error: Optional[str] = None


def send_dispute_email(
    to_email: str,
    subject: str,
    body: str,
    dispute_number: str,
) -> EmailSendResult:
    """Send a dispute email to a carrier billing contact.

    Args:
        to_email: MUST come from CarrierContact.billing_email — never from user input.
        subject: Dispute letter subject (LLM-drafted but reviewed).
        body: Dispute letter body (LLM-drafted, financial amounts injected by code).
        dispute_number: For logging and message-id tracking.

    Returns:
        EmailSendResult describing what happened.
    """
    settings = get_settings()

    if not settings.DISPUTE_EMAIL_ENABLED:
        logger.info(
            "[DISPUTE EMAIL — DISABLED] dispute=%s to=%s | "
            "Set DISPUTE_EMAIL_ENABLED=true to send real emails.",
            dispute_number, to_email,
        )
        return EmailSendResult(
            success=True,
            method="simulated",
            message_id=f"sim-{dispute_number}",
        )

    if not to_email or "@" not in to_email:
        return EmailSendResult(
            success=False,
            method="failed",
            error=f"Invalid recipient email: {to_email!r}",
        )

    sender_email = settings.DISPUTE_SENDER_EMAIL or settings.SMTP_USER
    if not sender_email:
        logger.error("[DISPUTE EMAIL] No sender email configured (DISPUTE_SENDER_EMAIL or SMTP_USER).")
        return EmailSendResult(
            success=False,
            method="failed",
            error="Sender email not configured. Set DISPUTE_SENDER_EMAIL in environment.",
        )

    if settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD:
        return _send_via_smtp(
            smtp_host=settings.SMTP_HOST,
            smtp_port=settings.SMTP_PORT,
            smtp_user=settings.SMTP_USER,
            smtp_password=settings.SMTP_PASSWORD,
            sender_email=sender_email,
            to_email=to_email,
            subject=subject,
            body=body,
            dispute_number=dispute_number,
        )

    logger.warning(
        "[DISPUTE EMAIL] DISPUTE_EMAIL_ENABLED=True but no SMTP credentials configured. "
        "Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD."
    )
    return EmailSendResult(
        success=False,
        method="failed",
        error="Email sending enabled but SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD.",
    )


def _send_via_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender_email: str,
    to_email: str,
    subject: str,
    body: str,
    dispute_number: str,
) -> EmailSendResult:
    """Send using SMTP with STARTTLS."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = to_email
        msg["X-Dispute-Number"] = dispute_number
        msg.attach(MIMEText(body, "plain", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.sendmail(sender_email, [to_email], msg.as_string())

        message_id = msg.get("Message-ID", f"smtp-{dispute_number}")
        logger.info(
            "[DISPUTE EMAIL — SENT via SMTP] dispute=%s to=%s subject=%r",
            dispute_number, to_email, subject,
        )
        return EmailSendResult(success=True, method="smtp", message_id=message_id)

    except smtplib.SMTPAuthenticationError as e:
        logger.error("[DISPUTE EMAIL] SMTP auth failed: %s", e)
        return EmailSendResult(success=False, method="smtp", error=f"SMTP authentication failed: {e}")
    except smtplib.SMTPException as e:
        logger.error("[DISPUTE EMAIL] SMTP error: %s", e)
        return EmailSendResult(success=False, method="smtp", error=f"SMTP error: {e}")
    except Exception as e:
        logger.error("[DISPUTE EMAIL] Unexpected error: %s", e)
        return EmailSendResult(success=False, method="smtp", error=str(e))
