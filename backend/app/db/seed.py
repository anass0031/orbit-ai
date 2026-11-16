"""Idempotent seed: one demo account per role.

Run: `python -m app.db.seed` (from backend/). Passwords come from ORBIT_DEMO_PASSWORD.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import session_scope
from app.models import User

DEMO_USERS = [
    ("admin@orbit.local", "Anass Aadoud", "admin"),
    ("engineer@orbit.local", "Zakaria Gouraguine", "data_engineer"),
    ("analyst@orbit.local", "Demo Analyst", "analyst"),
]


def seed(db: Session) -> dict:
    pw = hash_password(get_settings().demo_password)
    created = 0
    for email, name, role in DEMO_USERS:
        if db.scalar(select(User).where(User.email == email)) is None:
            db.add(User(email=email, full_name=name, role=role, password_hash=pw))
            created += 1
    return {"users_created": created}


if __name__ == "__main__":
    with session_scope() as s:
        print(seed(s))
