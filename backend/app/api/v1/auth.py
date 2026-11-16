import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.core.logging import log_event
from app.core.permissions import ROLE_LABELS, permissions_for
from app.core.ratelimit import login_throttle
from app.core.security import create_access_token, verify_password
from app.db.base import utcnow
from app.db.session import get_db
from app.models import User
from app.schemas.identity import LoginIn, MeOut, TokenOut, UserOut
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("orbit.auth")


def build_me(user: User) -> MeOut:
    return MeOut(user=UserOut.model_validate(user), role_label=ROLE_LABELS.get(user.role, user.role),
                 permissions=permissions_for(user.role))


@router.post("/login", response_model=TokenOut, summary="Sign in and receive a JWT access token")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    login_throttle.check(email, ip)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        login_throttle.failed(email, ip)
        log_event(logger, logging.WARNING, "auth.login_failed", email=email, ip=ip)
        if user is not None:
            audit(db, actor=user, action="auth.login_failed", resource_type="user", resource_id=user.id)
            db.commit()
        raise Unauthorized("Incorrect email or password")
    login_throttle.succeeded(email)
    user.last_login_at = utcnow()
    audit(db, actor=user, action="auth.login", resource_type="user", resource_id=user.id)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.role),
                    expires_in_minutes=get_settings().access_token_minutes, me=build_me(user))


@router.get("/me", response_model=MeOut, summary="Current user, role and permissions")
def me(user: User = Depends(get_current_user)):
    return build_me(user)
