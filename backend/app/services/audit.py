"""Audit trail: who did what, when (read by administrators)."""
from sqlalchemy.orm import Session

from app.core.logging import get_correlation_id
from app.models import AuditEvent, User


def audit(db: Session, *, actor: User | None, action: str, resource_type: str,
          resource_id: str | None = None, details: dict | None = None) -> AuditEvent:
    ev = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "system",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        correlation_id=get_correlation_id(),
    )
    db.add(ev)
    return ev
