from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ipalpha.api.schemas import HypePoint, IpDetail, MetricOut, OwnerOut
from ipalpha.db.models import HypeScore, Ip, IpOwnership, Metric
from ipalpha.db.session import get_db

router = APIRouter(prefix="/ips", tags=["ips"])


@router.get("/{ip_id}", response_model=IpDetail)
def get_ip(ip_id: int, db: Session = Depends(get_db)) -> IpDetail:
    ip = db.scalars(
        select(Ip)
        .where(Ip.id == ip_id)
        .options(selectinload(Ip.ownerships).selectinload(IpOwnership.company))
    ).first()
    if ip is None:
        raise HTTPException(404, f"unknown IP {ip_id}")

    latest_ts = (
        select(Metric.source, Metric.metric, func.max(Metric.ts).label("ts"))
        .where(Metric.ip_id == ip_id)
        .group_by(Metric.source, Metric.metric)
        .subquery()
    )
    metrics = db.scalars(
        select(Metric)
        .join(
            latest_ts,
            (Metric.source == latest_ts.c.source)
            & (Metric.metric == latest_ts.c.metric)
            & (Metric.ts == latest_ts.c.ts),
        )
        .where(Metric.ip_id == ip_id)
    ).all()
    hype = db.scalars(
        select(HypeScore).where(HypeScore.ip_id == ip_id).order_by(HypeScore.date)
    ).all()

    return IpDetail(
        id=ip.id,
        slug=ip.slug,
        name_en=ip.name_en,
        name_ja=ip.name_ja,
        aliases=ip.aliases or [],
        type=ip.type,
        steam_appid=ip.steam_appid,
        mal_id=ip.mal_id,
        owners=[
            OwnerOut(
                ticker=o.company.ticker,
                company_name=o.company.name,
                role=o.role,
                weight=o.weight,
                note=o.note,
            )
            for o in ip.ownerships
        ],
        latest_metrics=[
            MetricOut(source=m.source, metric=m.metric, value=m.value, ts=m.ts) for m in metrics
        ],
        hype=[HypePoint(date=h.date, score=h.score, components=h.components) for h in hype],
    )
