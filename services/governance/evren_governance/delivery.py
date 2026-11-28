"""Bildirim teslim kanalları (U8.9, ADR-0040): e-posta (SMTP) ve Teams (iş akışı gelen web kancası).

Sırlar (SMTP parolası, web kancası adresi) yalnızca yapılandırmada durur; hata iletileri onları içermez. Simülatör
yalnızca geliştirme ve testler içindir (``EVREN_NOTIFY_SIMULATE=1``): iletileri bellekte tutar.
"""
from __future__ import annotations

import json
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Protocol

from evren_core.connectors.http import HttpError, Transport, UrllibTransport

__all__ = ["DeliveryError", "EmailChannel", "MemoryEmail", "MemoryTeams", "SmtpEmail", "TeamsChannel",
           "WebhookTeams"]


class DeliveryError(RuntimeError):
    """Teslim başarısız. İleti sır, adres veya sunucu yanıtı içermez."""


class EmailChannel(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class TeamsChannel(Protocol):
    targets: frozenset[str] | None   # None: her hedef (simülatör)

    def send(self, target: str, card: Mapping[str, Any]) -> None: ...


@dataclass
class SmtpEmail:
    host: str
    port: int
    sender: str
    tls: str = "starttls"
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    ca_file: str | None = None
    timeout: float = 15.0

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self.sender.rsplit("@", 1)[-1])
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(body)
        context = ssl.create_default_context(cafile=self.ca_file) if self.ca_file else ssl.create_default_context()
        try:
            if self.tls == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with server:
                if self.tls == "starttls":
                    server.starttls(context=context)
                if self.user:
                    server.login(self.user, self.password or "")
                server.send_message(msg)
        except smtplib.SMTPRecipientsRefused:
            raise DeliveryError("Alıcı adresi sunucu tarafından reddedildi") from None
        except smtplib.SMTPAuthenticationError:
            raise DeliveryError("SMTP kimlik doğrulaması başarısız") from None
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise DeliveryError(f"E-posta gönderilemedi ({type(exc).__name__})") from None


@dataclass
class WebhookTeams:
    webhooks: Mapping[str, str] = field(repr=False)
    transport: Transport = field(default_factory=UrllibTransport)
    timeout: float = 15.0

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(self.webhooks)

    def send(self, target: str, card: Mapping[str, Any]) -> None:
        url = self.webhooks.get(target)
        if not url:
            raise DeliveryError(f"Teams hedefi tanımsız: {target}")
        body = json.dumps(card, ensure_ascii=False).encode()
        try:
            resp = self.transport.send("POST", url, {"Content-Type": "application/json"}, body, self.timeout)
        except HttpError as exc:
            raise DeliveryError(f"Teams'e ulaşılamadı ({exc})") from None
        if resp.status not in (200, 202):
            raise DeliveryError(f"Teams iletiyi kabul etmedi (durum {resp.status})")


@dataclass
class MemoryEmail:
    sent: list[tuple[str, str, str]] = field(default_factory=list)
    fail_for: set[str] = field(default_factory=set)

    def send(self, to: str, subject: str, body: str) -> None:
        if to in self.fail_for:
            raise DeliveryError("Simülatör: e-posta reddedildi")
        self.sent.append((to, subject, body))


@dataclass
class MemoryTeams:
    sent: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    targets: frozenset[str] | None = None

    def send(self, target: str, card: Mapping[str, Any]) -> None:
        self.sent.append((target, card))
