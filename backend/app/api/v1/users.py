"""Administration: users, roles and the audit log (administrator only)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.permissions import ROLE_LABELS, ROLES
from app.core.security import hash_password
from app.db.session import get_db
from app.models import AuditEvent, User
from app.schemas.identity import AuditEventOut, Page, UserCreate, UserOut, UserUpdate
from app.services.audit import audit

router = APIRouter(tags=["administration"])


@router.get("/roles", summary="The three platform roles")
def roles(_: User = Depends(require("users.manage"))):
    return [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES]


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require("users.manage"))):
    return db.scalars(select(User).order_by(User.created_at)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require("users.manage"))):
    email = body.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise Conflict("A user with this email already exists")
    u = User(email=email, full_name=body.full_name.strip(), role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    audit(db, actor=admin, action="user.create", resource_type="user", resource_id=u.id,
          details={"email": email, "role": body.role})
    db.commit()
    return u


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdate, db: Session = Depends(get_db),
                admin: User = Depends(require("users.manage"))):
    u = db.get(User, user_id)
    if u is None:
        raise NotFound("User not found")
    changes = body.model_dump(exclude_unset=True)
    if u.id == admin.id and (changes.get("role") not in (None, "admin") or changes.get("is_active") is False):
        raise ValidationFailed("You cannot remove your own administrator role or disable your own account")
    if "full_name" in changes and changes["full_name"]:
        u.full_name = changes["full_name"].strip()
    if changes.get("role"):
        u.role = changes["role"]
    if changes.get("is_active") is not None:
        u.is_active = changes["is_active"]
    if changes.get("password"):
        u.password_hash = hash_password(changes["password"])
    audit(db, actor=admin, action="user.update", resource_type="user", resource_id=u.id,
          details={k: ("***" if k == "password" else v) for k, v in changes.items()})
    db.commit()
    return u


@router.get("/audit", response_model=Page, summary="Audit log, newest first")
def audit_log(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), action: str | None = None,
              db: Session = Depends(get_db), _: User = Depends(require("audit.read"))):
    q = select(AuditEvent)
    if action:
        q = q.where(AuditEvent.action.startswith(action))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[AuditEventOut.model_validate(r) for r in rows], total=total or 0, limit=limit, offset=offset)
