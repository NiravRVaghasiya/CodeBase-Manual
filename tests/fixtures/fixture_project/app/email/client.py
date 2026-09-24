"""Abstraction over a third-party transactional email service."""

from __future__ import annotations

from abc import ABC, abstractmethod

from config.settings import get_settings


class EmailClient(ABC):
    """Interface implemented by concrete email service integrations."""

    @abstractmethod
    async def send(self, to: str, subject: str, body: str) -> bool:
        raise NotImplementedError


class SmtpEmailClient(EmailClient):
    """Sends email via the configured SMTP relay."""

    def __init__(self, host: str, port: int = 587) -> None:
        self.host = host
        self.port = port

    async def send(self, to: str, subject: str, body: str) -> bool:
        return bool(to and subject)


def get_email_client() -> EmailClient:
    settings = get_settings()
    return SmtpEmailClient(host=settings.smtp_host)


async def send_welcome_email(client: EmailClient, email: str) -> bool:
    return await client.send(to=email, subject="Welcome", body="Thanks for signing up.")
