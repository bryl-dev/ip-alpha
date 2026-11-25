import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ipalpha.db.models import LlmCall
from ipalpha.db.session import get_db

router = APIRouter(prefix="/metrics", tags=["metrics"])


class NodeMetrics(BaseModel):
    node: str
    calls: int
    failures: int
    p50_latency_ms: int
    p95_latency_ms: int
    tokens: int
    cost_usd: float


class LlmMetrics(BaseModel):
    days: int
    calls: int
    failures: int
    prompt_tokens: int
    completion_tokens: int
    gpu_seconds: float
    cost_usd: float
    by_node: list[NodeMetrics]


@router.get("/llm", response_model=LlmMetrics)
def llm_metrics(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)) -> LlmMetrics:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    filt = LlmCall.created_at >= since

    totals = db.execute(
        select(
            func.count().label("calls"),
            func.coalesce(func.sum(case((LlmCall.status != "ok", 1), else_=0)), 0).label(
                "failures"
            ),
            func.coalesce(func.sum(LlmCall.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LlmCall.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(LlmCall.gpu_seconds), 0.0).label("gpu_seconds"),
            func.coalesce(func.sum(LlmCall.cost_usd), 0.0).label("cost_usd"),
        ).where(filt)
    ).one()

    by_node_rows = db.execute(
        select(
            LlmCall.node.label("node"),
            func.count().label("calls"),
            func.coalesce(func.sum(case((LlmCall.status != "ok", 1), else_=0)), 0).label(
                "failures"
            ),
            func.cast(
                func.percentile_cont(0.5).within_group(LlmCall.latency_ms),
                LlmCall.latency_ms.type,
            ).label("p50"),
            func.cast(
                func.percentile_cont(0.95).within_group(LlmCall.latency_ms),
                LlmCall.latency_ms.type,
            ).label("p95"),
            (
                func.coalesce(func.sum(LlmCall.prompt_tokens), 0)
                + func.coalesce(func.sum(LlmCall.completion_tokens), 0)
            ).label("tokens"),
            func.coalesce(func.sum(LlmCall.cost_usd), 0.0).label("cost_usd"),
        )
        .where(filt)
        .group_by(LlmCall.node)
    ).all()

    by_node = [
        NodeMetrics(
            node=r.node,
            calls=int(r.calls or 0),
            failures=int(r.failures or 0),
            p50_latency_ms=int(r.p50 or 0),
            p95_latency_ms=int(r.p95 or 0),
            tokens=int(r.tokens or 0),
            cost_usd=float(r.cost_usd or 0),
        )
        for r in by_node_rows
    ]

    return LlmMetrics(
        days=days,
        calls=int(totals.calls or 0),
        failures=int(totals.failures or 0),
        prompt_tokens=int(totals.prompt_tokens or 0),
        completion_tokens=int(totals.completion_tokens or 0),
        gpu_seconds=float(totals.gpu_seconds or 0),
        cost_usd=float(totals.cost_usd or 0),
        by_node=sorted(by_node, key=lambda n: n.node),
    )
