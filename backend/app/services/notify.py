"""Deliveries shared by pipeline notifications and destinations: in-app notifications, e-mail (SMTP), webhooks.

- In-app: one `Notification` row per user of the chosen roles (shown by the bell in the sidebar).
- E-mail: SMTP settings `ORBIT_SMTP_*`; nothing is sent when `ORBIT_SMTP_HOST` is empty (the delivery is reported
  as skipped, never silently ignored).
- Webhook: JSON POST compatible with Slack and Microsoft Teams incoming webhooks (`{"text": ...}`). Link-local /
  cloud-metadata addresses are refused, like for the REST connector.
"""
from __future__ import annotations

import ipaddress
import smtplib
import socket
from email.message import EmailMessage
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import ROLES
from app.models import Notification, User

LEVELS = ("info", "success", "warning", "error")


class DeliveryError(Exception):
    pass


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DeliveryError("Give an http(s) URL")
    host = parts.hostname.lower()
    if host in ("metadata.google.internal", "metadata"):
        raise DeliveryError(f"Host '{host}' is not allowed")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DeliveryError(f"Cannot resolve host '{host}'") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise DeliveryError(f"Host '{host}' resolves to a link-local / metadata address ({ip}), which is not allowed")


def http_send(method: str, url: str, *, json=None, content: bytes | None = None, headers: dict | None = None,
              auth=None, retries: int = 2, timeout: float = 30.0) -> httpx.Response:
    """One HTTP delivery with retries on network errors and 5xx answers."""
    check_url(url)
    last: Exception | None = None
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(retries + 1):
            try:
                r = client.request(method, url, json=json, content=content, headers=headers, auth=auth)
                if r.status_code < 500:
                    return r
                last = DeliveryError(f"HTTP {r.status_code}: {r.text[:200]}")
            except httpx.HTTPError as exc:
                last = exc
    raise DeliveryError(f"{method} {url} failed: {last}")


def smtp_configured() -> bool:
    return bool(get_settings().smtp_host)


def send_email(to: list[str], subject: str, text: str, html: str | None = None,
               attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise DeliveryError("E-mail is not configured: set ORBIT_SMTP_HOST (and ORBIT_SMTP_PORT / USER / PASSWORD) in .env")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, ", ".join(to), subject[:200]
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments or []:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_tls:
                try:
                    smtp.starttls()
                except smtplib.SMTPNotSupportedError:
                    pass  # local relays (e.g. Mailpit) may not offer TLS
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"SMTP delivery to {', '.join(to)} failed: {exc}") from exc


def in_app(db: Session, *, roles: list[str], title: str, message: str, level: str = "info", link: str | None = None,
           pipeline_id: str | None = None, run_id: str | None = None, extra_user_ids: list[str] | None = None) -> int:
    roles = [r for r in roles or [] if r in ROLES]
    users = list(db.scalars(select(User).where(User.is_active.is_(True), User.role.in_(roles)))) if roles else []
    ids = {u.id for u in users} | set(extra_user_ids or [])
    for uid in ids:
        db.add(Notification(user_id=uid, level=level if level in LEVELS else "info", title=title[:200], message=message,
                            link=link, pipeline_id=pipeline_id, run_id=run_id))
    return len(ids)


def deliver(db: Session, channels: dict, *, title: str, message: str, level: str, link: str | None,
            pipeline_id: str | None = None, run_id: str | None = None, owner_id: str | None = None) -> list[dict]:
    """Send one event on every configured channel. Returns [{channel, status, detail}] (never raises)."""
    out = []
    url = f"{get_settings().public_url.rstrip('/')}{link}" if link else None
    if channels.get("in_app", True):
        n = in_app(db, roles=channels.get("roles") or ["admin", "data_engineer"], title=title, message=message, level=level,
                   link=link, pipeline_id=pipeline_id, run_id=run_id, extra_user_ids=[owner_id] if owner_id else None)
        out.append({"channel": "in_app", "status": "sent", "detail": f"{n} user(s)"})
    emails = [e for e in channels.get("emails") or [] if e]
    if emails:
        try:
            send_email(emails, f"[ORBIT] {title}", f"{message}\n\n{url or ''}".strip())
            out.append({"channel": "email", "status": "sent", "detail": ", ".join(emails)})
        except DeliveryError as exc:
            out.append({"channel": "email", "status": "failed", "detail": str(exc)})
    if channels.get("webhook_url"):
        try:
            r = http_send("POST", channels["webhook_url"], json={"text": f"*{title}*\n{message}" + (f"\n{url}" if url else "")},
                          retries=1, timeout=15)
            out.append({"channel": "webhook", "status": "sent" if r.status_code < 400 else "failed", "detail": f"HTTP {r.status_code}"})
        except DeliveryError as exc:
            out.append({"channel": "webhook", "status": "failed", "detail": str(exc)})
    return out
