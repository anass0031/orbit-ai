"""FastAPI dependencies: current user (JWT) and permission checks (role-based access control)."""
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, Unauthorized
from app.core.permissions import ROLE_LABELS, has_permission
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise Unauthorized("Authentication required")
    payload = decode_access_token(creds.credentials)
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise Unauthorized("User not found or disabled")
    return user


def require(permission: str):
    """Dependency factory: the current user must hold `permission` (see app/core/permissions.py)."""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise Forbidden(f"Your role ({ROLE_LABELS.get(user.role, user.role)}) does not allow '{permission}'")
        return user

    return _dep
