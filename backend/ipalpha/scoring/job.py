"""Nightly job: recompute IP and company Hype Scores plus divergence from the full history."""

import logging
import math

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.db.models import Company, Document, HypeScore, IpOwnership, Metric, StockPrice
from ipalpha.scoring.divergence import divergence
from ipalpha.scoring.hype import company_hype, ip_hype

log = logging.getLogger(__name__)


def _load_metrics(session: Session) -> pd.DataFrame:
    jst_date = func.date(func.timezone("Asia/Tokyo", Metric.ts))
    rows = session.execute(
        select(Metric.ip_id, Metric.metric, jst_date.label("date"), Metric.value)
    ).all()
    return pd.DataFrame(rows, columns=["ip_id", "metric", "date", "value"])


def _load_mentions(session: Session) -> pd.DataFrame:
    """Daily social mention counts, with explicit zeros on days we collected but saw nothing."""
    jst_date = func.date(func.timezone("Asia/Tokyo", Document.published_at))
    social = Document.source.in_(("reddit", "bluesky"))
    rows = session.execute(
        select(Document.ip_id, jst_date.label("date"), func.count())
        .where(social, Document.ip_id.is_not(None))
        .group_by(Document.ip_id, jst_date)
    ).all()
    span = session.execute(select(func.min(jst_date), func.max(jst_date)).where(social)).one()
    if not rows or span[0] is None:
        return pd.DataFrame(columns=["ip_id", "metric", "date", "value"])

    counts = pd.DataFrame(rows, columns=["ip_id", "date", "value"])
    counts["date"] = pd.to_datetime(counts["date"])
    days = pd.date_range(span[0], span[1], freq="D")
    ip_ids = session.scalars(select(IpOwnership.ip_id).distinct()).all()
    grid = pd.MultiIndex.from_product([ip_ids, days], names=["ip_id", "date"])
    full = counts.set_index(["ip_id", "date"])["value"].reindex(grid, fill_value=0).reset_index()
    full["metric"] = "mention_count"
    full["date"] = full["date"].dt.date
    return full


def _load_closes(session: Session, ticker: str) -> pd.Series:
    rows = session.execute(
        select(StockPrice.date, StockPrice.close).where(StockPrice.ticker == ticker)
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d): c for d, c in rows}).sort_index()
    return s


def _clean(components: dict) -> dict:
    return {k: round(float(v), 4) for k, v in components.items() if not math.isnan(v)}


def run(session: Session) -> dict[str, int]:
    metrics = pd.concat([_load_metrics(session), _load_mentions(session)], ignore_index=True)

    ip_scores: dict[int, pd.Series] = {}
    rows: list[HypeScore] = []
    for ip_id, group in metrics.groupby("ip_id"):
        hype = ip_hype(group)
        if hype.empty:
            continue
        ip_scores[int(ip_id)] = hype["score"]
        for date, rec in hype.iterrows():
            parts = rec.drop("score").to_dict()
            rows.append(
                HypeScore(
                    ip_id=int(ip_id),
                    date=date.date(),
                    score=float(rec["score"]),
                    components=_clean(parts),
                )
            )

    benchmark = _load_closes(session, get_settings().benchmark_ticker)
    company_rows = 0
    for company in session.scalars(select(Company)):
        weights = {o.ip_id: o.weight for o in company.ownerships if o.ip_id in ip_scores}
        hype = company_hype({i: ip_scores[i] for i in weights}, weights)
        if hype.empty:
            continue
        div = divergence(hype, _load_closes(session, company.ticker), benchmark)
        for date, score in hype.items():
            d = div.loc[date] if date in div.index else None
            rows.append(
                HypeScore(
                    company_id=company.id,
                    date=date.date(),
                    score=float(score),
                    divergence=float(d["divergence"]) if d is not None else None,
                    components=_clean({"return_z": d["return_z"]}) if d is not None else {},
                )
            )
            company_rows += 1

    session.execute(delete(HypeScore))
    session.add_all(rows)
    session.flush()
    log.info("hype scores: %d IP rows, %d company rows", len(rows) - company_rows, company_rows)
    return {"ip_rows": len(rows) - company_rows, "company_rows": company_rows}
