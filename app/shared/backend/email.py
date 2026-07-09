from __future__ import annotations

import smtplib
import threading
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from app.shared.utils.env import get_email_env_config


MIN_INTERVAL_SECONDS = 3.0


class EmailRateLimiter:
    """Singleton rate limiter shared by all email sends in this process."""

    _instance: EmailRateLimiter | None = None
    _instance_lock = threading.Lock()

    def __new__(cls, min_interval: float = MIN_INTERVAL_SECONDS) -> EmailRateLimiter:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._last_sent_at = 0.0
                instance._min_interval = max(float(min_interval), MIN_INTERVAL_SECONDS)
                cls._instance = instance
            else:
                # Keep singleton behavior while allowing callers to raise interval.
                cls._instance._min_interval = max(
                    cls._instance._min_interval,
                    float(min_interval),
                    MIN_INTERVAL_SECONDS,
                )
            return cls._instance

    @property
    def min_interval(self) -> float:
        return self._min_interval

    def wait_for_slot(self) -> None:
        """Block until enough time has elapsed since the last successful send."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_sent_at
            wait_seconds = self._min_interval - elapsed
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_sent_at = time.monotonic()


def get_email_rate_limiter(min_interval: float = MIN_INTERVAL_SECONDS) -> EmailRateLimiter:
    return EmailRateLimiter(min_interval=min_interval)


def send_html_email(
    to_address: str | list[str],
    subject: str,
    html_body: str,
    *,
    from_address: str | None = None,
    from_name: str | None = None,
    min_interval: float = MIN_INTERVAL_SECONDS,
) -> None:
    """Send an HTML email using SMTP credentials from env.

    Env source: agent.utils.env.get_email_env_config()
    """
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("`subject` must be a non-empty string.")
    if not isinstance(html_body, str) or not html_body.strip():
        raise ValueError("`html_body` must be a non-empty string.")

    config = get_email_env_config()
    limiter = get_email_rate_limiter(min_interval=min_interval)
    limiter.wait_for_slot()

    sender_address = from_address.strip() if isinstance(from_address, str) and from_address.strip() else config.from_address
    sender_name = from_name.strip() if isinstance(from_name, str) and from_name.strip() else config.from_name
    to_header = ",".join(to_address) if isinstance(to_address, list) else to_address

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr([sender_name, sender_address])
    msg["To"] = to_header
    msg["Subject"] = Header(subject.strip(), "utf-8")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
        server.starttls()
        server.login(config.username, config.password)
        server.sendmail(config.username, to_address, msg.as_string())
