"""Read-only data access shared by the Analyst Agent and the chat graph."""

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ipalpha.config import get_settings
from ipalpha.db.models import Company, Document, HypeScore, Ip, IpOwnership, StockPrice, Thesis
from ipalpha.ingestion.common import JST
from ipalpha.llm import LLM, LLM_UNAVAILABLE

log = logging.getLogger(__name__)

SNIPPET_CHARS = 500


@dataclass
class DocHit:
    id: int
    source: str
    url: str | None
    published_at: dt.datetime
    ip_name: str | None
    text: str
    summary: str | None
    sentiment: float | None
    event_type: str | None
    similarity: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_line(self) -> str:
        meta = [self.source, self.published_at.astimezone(JST).strftime("%Y-%m-%d")]
        if self.ip_name:
            meta.append(self.ip_name)
        if self.event_type:
            meta.append(self.event_type)
        if self.sentiment is not None:
            meta.append(f"sentiment {self.sentiment:+.1f}")
        return f"[{self.id}] ({', '.join(meta)}) {self.text}"


def _hit(doc: Document, ip_name: str | None, distance: float | None = None) -> DocHit:
    return DocHit(
        id=doc.id,
        source=doc.source,
        url=doc.url,
        published_at=doc.published_at,
        ip_name=ip_name,
        text=" ".join((doc.text_en or doc.text_original).split())[:SNIPPET_CHARS],
        summary=doc.summary,
        sentiment=doc.sentiment,
        event_type=doc.event_type,
        similarity=None if distance is None else round(1 - distance, 3),
    )


def search_documents(
    session: Session,
    llm: LLM,
    query: str,
    *,
    ip_ids: list[int] | None = None,
    since: dt.datetime | None = None,
    limit: int = 8,
) -> list[DocHit]:
    """Nearest documents to `query` by embedding, topped up with the most recent ones (which
    also covers the case where nothing is embedded yet or the embedding server is down)."""
    base = select(Document, Ip.name_en).outerjoin(Ip, Ip.id == Document.ip_id)
    if ip_ids is not None:
        base = base.where(Document.ip_id.in_(ip_ids))
    if since is not None:
        base = base.where(Document.published_at >= since)

    hits: list[DocHit] = []
    seen_text: set[str] = set()

    def add(doc: Document, ip_name: str | None, distance: float | None = None) -> None:
        if doc.text_original not in seen_text and len(hits) < limit:
            seen_text.add(doc.text_original)
            hits.append(_hit(doc, ip_name, distance))

    try:
        vector = llm.embed([query])[0]
    except LLM_UNAVAILABLE as exc:
        log.warning("embedding unavailable, falling back to recent documents: %s", exc)
        vector = None
    if vector is not None:
        distance = Document.embedding.cosine_distance(vector).label("distance")
        for doc, ip_name, dist in session.execute(
            base.add_columns(distance)
            .where(Document.embedding.is_not(None))
            .order_by(distance)
            .limit(limit * 3)
        ):
            add(doc, ip_name, dist)
    if len(hits) < limit:
        for doc, ip_name in session.execute(
            base.order_by(Document.published_at.desc()).limit(limit * 3)
        ):
            add(doc, ip_name)
    return hits


@dataclass
class IpHype:
    name: str
    hype: float
    components: dict[str, float]


@dataclass
class Snapshot:
    ticker: str
    name: str
    currency: str
    as_of: dt.date | None = None
    close: float | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    benchmark_5d: float | None = None
    hype: float | None = None
    hype_5d_ago: float | None = None
    divergence: float | None = None
    return_z: float | None = None
    hype_date: dt.date | None = None
    top_ips: list[IpHype] = field(default_factory=list)

    def prompt_block(self) -> str:
        # Small models misread signs, so directions are spelled out in words.
        def pct(v: float | None) -> str:
            if v is None:
                return "n/a"
            return f"{v:+.1%} ({'up' if v > 0 else 'down' if v < 0 else 'flat'})"

        def num(v: float | None) -> str:
            return "n/a" if v is None else f"{v:+.2f}"

        if self.divergence is None:
            reading = ""
        elif self.divergence > 0:
            reading = " (fan hype is running ahead of the stock price)"
        else:
            reading = " (the stock price is running ahead of fan hype)"
        lines = [
            f"{self.name} ({self.ticker})",
            f"- Last close {self.close if self.close is not None else 'n/a'} {self.currency} "
            f"on {self.as_of or 'n/a'}; 1-day return {pct(self.return_1d)}; "
            f"5-day return {pct(self.return_5d)} vs Nikkei 225 {pct(self.benchmark_5d)}",
            f"- Company Hype Score {num(self.hype)} on {self.hype_date or 'n/a'} "
            f"(5 days earlier {num(self.hype_5d_ago)}); divergence {num(self.divergence)}"
            f"{reading}; excess-return z {num(self.return_z)}",
        ]
        for ip in self.top_ips:
            parts = ", ".join(f"{k} {v:+.1f}" for k, v in ip.components.items())
            lines.append(f"- IP {ip.name}: hype {ip.hype:+.2f} ({parts})")
        return "\n".join(lines)


def _closes(session: Session, ticker: str, until: dt.date | None, n: int) -> list[StockPrice]:
    stmt = select(StockPrice).where(StockPrice.ticker == ticker)
    if until is not None:
        stmt = stmt.where(StockPrice.date <= until)
    return list(session.scalars(stmt.order_by(StockPrice.date.desc()).limit(n)))


def _ret(closes: list[StockPrice], lag: int) -> float | None:
    return closes[0].close / closes[lag].close - 1 if len(closes) > lag else None


def company_ip_ids(company: Company) -> list[int]:
    return [o.ip_id for o in company.ownerships]


def snapshot(session: Session, company: Company, until: dt.date | None = None) -> Snapshot:
    snap = Snapshot(company.ticker, company.name, company.currency)
    closes = _closes(session, company.ticker, until, 6)
    if closes:
        snap.as_of, snap.close = closes[0].date, closes[0].close
        snap.return_1d, snap.return_5d = _ret(closes, 1), _ret(closes, 5)
        bench = _closes(session, get_settings().benchmark_ticker, snap.as_of, 6)
        snap.benchmark_5d = _ret(bench, 5)

    hype_stmt = select(HypeScore).where(HypeScore.company_id == company.id)
    if until is not None:
        hype_stmt = hype_stmt.where(HypeScore.date <= until)
    hype = list(session.scalars(hype_stmt.order_by(HypeScore.date.desc()).limit(6)))
    if hype:
        latest = hype[0]
        snap.hype_date, snap.hype, snap.divergence = latest.date, latest.score, latest.divergence
        snap.return_z = latest.components.get("return_z")
        snap.hype_5d_ago = hype[5].score if len(hype) > 5 else None
        rows = session.execute(
            select(Ip.name_en, HypeScore.score, HypeScore.components)
            .join(HypeScore, HypeScore.ip_id == Ip.id)
            .where(HypeScore.ip_id.in_(company_ip_ids(company)), HypeScore.date == latest.date)
            .order_by(HypeScore.score.desc())
            .limit(3)
        ).all()
        snap.top_ips = [IpHype(name, score, comps) for name, score, comps in rows]
    return snap


def series_block(session: Session, company: Company, days: int = 10) -> str:
    closes = list(reversed(_closes(session, company.ticker, None, days + 1)))
    if not closes:
        return ""
    hype = {
        h.date: h
        for h in session.scalars(
            select(HypeScore).where(
                HypeScore.company_id == company.id, HypeScore.date >= closes[0].date
            )
        )
    }
    lines = ["date | close | daily return | hype | divergence"]
    for prev, cur in zip(closes, closes[1:], strict=False):
        h = hype.get(cur.date)
        lines.append(
            f"{cur.date} | {cur.close:,.0f} | {cur.close / prev.close - 1:+.1%} | "
            f"{f'{h.score:+.2f}' if h else 'n/a'} | "
            f"{f'{h.divergence:+.2f}' if h and h.divergence is not None else 'n/a'}"
        )
    return "\n".join(lines)


def recent_theses(session: Session, ticker: str, limit: int = 2) -> list[Thesis]:
    return list(
        session.scalars(
            select(Thesis)
            .where(Thesis.ticker == ticker)
            .order_by(Thesis.date.desc(), Thesis.created_at.desc())
            .limit(limit)
        )
    )


def companies_with_ownerships(session: Session) -> list[Company]:
    return list(
        session.scalars(
            select(Company)
            .options(selectinload(Company.ownerships).selectinload(IpOwnership.ip))
            .order_by(Company.ticker)
        )
    )
