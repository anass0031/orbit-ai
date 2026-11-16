import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db, get_engine
from app.lakehouse.storage import get_storage
from app.models import Dataset, FileImport, User

router = APIRouter(tags=["health"])


def _probe(fn) -> dict:
    t0 = time.perf_counter()
    try:
        detail = fn()
        return {"status": "up", "detail": detail, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - any failure means the component is down
        return {"status": "down", "detail": str(exc)[:200], "latency_ms": None}


def _db() -> str:
    with get_engine().connect() as c:
        c.execute(text("SELECT 1"))
        return c.dialect.name


@router.get("/health", summary="Liveness and readiness (database + object storage). Public, used by Docker.")
def health():
    components = {"database": _probe(_db), "object_storage": _probe(lambda: get_storage().ping())}
    ok = all(c["status"] == "up" for c in components.values())
    return {"status": "ok" if ok else "degraded", "storage_backend": get_settings().storage_backend,
            "components": components}


@router.get("/overview", summary="Counters for the home dashboard")
def overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    by_status = dict(db.execute(select(FileImport.status, func.count()).group_by(FileImport.status)).all())
    return {
        "imports_total": sum(by_status.values()),
        "imports_by_status": by_status,
        "bronze_datasets": db.scalar(select(func.count()).where(Dataset.layer == "bronze")) or 0,
        "bronze_rows": db.scalar(select(func.coalesce(func.sum(Dataset.row_count), 0)).where(Dataset.layer == "bronze")),
        "bronze_bytes": db.scalar(select(func.coalesce(func.sum(Dataset.size_bytes), 0)).where(Dataset.layer == "bronze")),
        "users_active": db.scalar(select(func.count()).where(User.is_active.is_(True))) or 0,
    }
