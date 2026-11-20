"""In-app notifications of the signed-in user (pipeline failures, successes, quality warnings, SLA breaches)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import NotFound
from app.db.base import utcnow
from app.db.session import get_db
from app.models import Notification, User

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(n: Notification) -> dict:
    return {"id": n.id, "level": n.level, "title": n.title, "message": n.message, "link": n.link, "pipeline_id": n.pipeline_id,
            "run_id": n.run_id, "created_at": n.created_at, "read": n.read_at is not None}


@router.get("")
def mine(unread: bool = False, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    items = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    count = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id,
                                                                            Notification.read_at.is_(None))) or 0
    return {"unread": count, "items": [_out(n) for n in items]}


@router.post("/{notification_id}/read")
def read(notification_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise NotFound("Notification not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return _out(n)


@router.post("/read-all")
def read_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    res = db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
                     .values(read_at=utcnow()))
    db.commit()
    return {"marked": res.rowcount}
