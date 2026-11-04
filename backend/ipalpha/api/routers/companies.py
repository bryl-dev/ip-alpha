import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ipalpha.api.schemas import CompanyDetail, CompanySummary, IpSummary, SeriesPoint
from ipalpha.db.models import Company, HypeScore, IpOwnership, StockPrice
from ipalpha.db.session import get_db

router = APIRouter(prefix="/companies", tags=["companies"])


def _summary(db: Session, company: Company) -> CompanySummary:
    closes = db.execute(
        select(StockPrice.date, StockPrice.close)
        .where(StockPrice.ticker == company.ticker)
        .order_by(StockPrice.date.desc())
        .limit(6)
    ).all()
    latest = db.scalars(
        select(HypeScore)
        .where(HypeScore.company_id == company.id)
        .order_by(HypeScore.date.desc())
        .limit(1)
    ).first()
    return CompanySummary(
        ticker=company.ticker,
        name=company.name,
        name_ja=company.name_ja,
        currency=company.currency,
        ip_count=len(company.ownerships),
        last_close=closes[0].close if closes else None,
        last_close_date=closes[0].date if closes else None,
        return_5d=closes[0].close / closes[5].close - 1 if len(closes) == 6 else None,
        hype=latest.score if latest else None,
        divergence=latest.divergence if latest else None,
    )


def _get_company(db: Session, ticker: str) -> Company:
    company = db.scalars(
        select(Company)
        .where(Company.ticker == ticker)
        .options(selectinload(Company.ownerships).selectinload(IpOwnership.ip))
    ).first()
    if company is None:
        raise HTTPException(404, f"unknown ticker {ticker}")
    return company


@router.get("", response_model=list[CompanySummary])
def list_companies(db: Session = Depends(get_db)) -> list[CompanySummary]:
    companies = db.scalars(
        select(Company).options(selectinload(Company.ownerships)).order_by(Company.ticker)
    ).all()
    return [_summary(db, c) for c in companies]


@router.get("/{ticker}", response_model=CompanyDetail)
def get_company(ticker: str, db: Session = Depends(get_db)) -> CompanyDetail:
    company = _get_company(db, ticker)
    ip_ids = [o.ip_id for o in company.ownerships]

    latest_date = (
        select(HypeScore.ip_id, func.max(HypeScore.date).label("date"))
        .where(HypeScore.ip_id.in_(ip_ids))
        .group_by(HypeScore.ip_id)
        .subquery()
    )
    latest = {
        h.ip_id: h
        for h in db.scalars(
            select(HypeScore).join(
                latest_date,
                (HypeScore.ip_id == latest_date.c.ip_id) & (HypeScore.date == latest_date.c.date),
            )
        )
    }

    ips = []
    for o in company.ownerships:
        h = latest.get(o.ip_id)
        ips.append(
            IpSummary(
                id=o.ip.id,
                slug=o.ip.slug,
                name_en=o.ip.name_en,
                name_ja=o.ip.name_ja,
                type=o.ip.type,
                role=o.role,
                weight=o.weight,
                hype=h.score if h else None,
                hype_date=h.date if h else None,
                components=h.components if h else {},
            )
        )
    ips.sort(key=lambda i: (i.hype is None, -(i.hype or 0)))
    return CompanyDetail(**_summary(db, company).model_dump(), ips=ips)


@router.get("/{ticker}/series", response_model=list[SeriesPoint])
def company_series(
    ticker: str,
    from_: dt.date | None = Query(None, alias="from"),
    to: dt.date | None = None,
    db: Session = Depends(get_db),
) -> list[SeriesPoint]:
    company = _get_company(db, ticker)
    to = to or dt.date.today()
    from_ = from_ or to - dt.timedelta(days=180)

    points: dict[dt.date, SeriesPoint] = {}
    for d, close in db.execute(
        select(StockPrice.date, StockPrice.close).where(
            StockPrice.ticker == ticker, StockPrice.date.between(from_, to)
        )
    ):
        points[d] = SeriesPoint(date=d, close=close)
    for h in db.scalars(
        select(HypeScore).where(
            HypeScore.company_id == company.id, HypeScore.date.between(from_, to)
        )
    ):
        p = points.setdefault(h.date, SeriesPoint(date=h.date))
        p.hype, p.divergence = h.score, h.divergence
    return [points[d] for d in sorted(points)]
