import time

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine
from app.lakehouse.storage import get_storage

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


def _queue() -> str:
    s = get_settings()
    if s.tasks_eager:
        return "in-process (ORBIT_TASKS_EAGER=true)"
    import redis

    r = redis.Redis.from_url(s.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        r.ping()
        return f"redis, {r.llen('celery')} job(s) waiting"
    finally:
        r.close()


@router.get("/health", summary="Liveness and readiness (database, object storage, job queue). Public, used by Docker.")
def health():
    components = {"database": _probe(_db), "object_storage": _probe(lambda: get_storage().ping()), "job_queue": _probe(_queue)}
    ok = all(c["status"] == "up" for c in components.values())
    return {"status": "ok" if ok else "degraded", "storage_backend": get_settings().storage_backend, "components": components}
