import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.db.session import get_db
from ipalpha.infra import redis as redis_infra

router = APIRouter(tags=["health"])


class ReadyOut(BaseModel):
    status: str  # ok | degraded
    checks: dict[str, bool]


def _llm_models_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/v1"):
        b = b[:-3]
    return f"{b}/models"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready(response: Response, db: Session = Depends(get_db)) -> ReadyOut:
    ok_db = bool(db.execute(text("SELECT 1")).scalar())
    ok_redis = redis_infra.redis_ok()
    llm = False
    try:
        r = httpx.get(_llm_models_url(get_settings().llm_base_url), timeout=2.0)
        llm = r.status_code < 500
    except Exception:
        llm = False
    checks = {"db": ok_db, "redis": ok_redis, "llm": llm}
    if not ok_db or not ok_redis:
        response.status_code = 503
        return ReadyOut(status="degraded", checks=checks)
    if not llm:
        return ReadyOut(status="degraded", checks=checks)
    return ReadyOut(status="ok", checks=checks)
