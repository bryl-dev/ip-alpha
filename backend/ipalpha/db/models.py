import datetime as dt
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    pass


class IpType(StrEnum):
    game = "game"
    anime = "anime"
    light_novel = "light_novel"
    manga = "manga"
    franchise = "franchise"


class OwnershipRole(StrEnum):
    publisher = "publisher"
    developer = "developer"
    licensor = "licensor"
    parent = "parent"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    name_ja: Mapped[str | None] = mapped_column(String(200))
    exchange: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))

    ownerships: Mapped[list["IpOwnership"]] = relationship(back_populates="company")


class Ip(Base):
    __tablename__ = "ips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name_en: Mapped[str] = mapped_column(String(200))
    name_ja: Mapped[str | None] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    type: Mapped[str] = mapped_column(String(20))
    steam_appid: Mapped[int | None] = mapped_column(Integer)
    mal_id: Mapped[int | None] = mapped_column(Integer)

    ownerships: Mapped[list["IpOwnership"]] = relationship(
        back_populates="ip", cascade="all, delete-orphan"
    )


class IpOwnership(Base):
    __tablename__ = "ip_ownership"
    __table_args__ = (UniqueConstraint("ip_id", "company_id", "role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip_id: Mapped[int] = mapped_column(ForeignKey("ips.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    weight: Mapped[float] = mapped_column(Float)
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    ip: Mapped[Ip] = relationship(back_populates="ownerships")
    company: Mapped[Company] = relationship(back_populates="ownerships")


class RawEvent(Base):
    __tablename__ = "raw_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (
        UniqueConstraint("ip_id", "source", "metric", "ts"),
        Index("ix_metrics_ip_metric_ts", "ip_id", "metric", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ip_id: Mapped[int] = mapped_column(ForeignKey("ips.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    metric: Mapped[str] = mapped_column(String(32))
    value: Mapped[float] = mapped_column(Float)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ip_id: Mapped[int | None] = mapped_column(ForeignKey("ips.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(300), unique=True)
    url: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(String(8))
    text_original: Mapped[str] = mapped_column(Text)
    text_en: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[float | None] = mapped_column(Float)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    published_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Filled by the normalization agent.
    event_type: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    ip_mentions: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    normalized_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # How the IP mapper resolved ip_id: alias, embedding, llm, unknown, or none (no mentions).
    mapping: Mapped[str | None] = mapped_column(String(16))


class StockPrice(Base):
    __tablename__ = "stock_prices"
    __table_args__ = (UniqueConstraint("ticker", "date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)


class HypeScore(Base):
    """One row per IP per day, or per company per day (exactly one of ip_id/company_id is set)."""

    __tablename__ = "hype_scores"
    __table_args__ = (
        UniqueConstraint("ip_id", "date"),
        UniqueConstraint("company_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ip_id: Mapped[int | None] = mapped_column(ForeignKey("ips.id", ondelete="CASCADE"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)
    divergence: Mapped[float | None] = mapped_column(Float)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Thesis(Base):
    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    divergence: Mapped[float | None] = mapped_column(Float)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(100))
    tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IpCandidate(Base):
    """Titles the mapper saw but couldn't tie to a tracked IP; a human decides what to add."""

    __tablename__ = "ip_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    example_document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    first_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DeadLetter(Base):
    """An item a pipeline stage gave up on, kept for inspection instead of crashing the run."""

    __tablename__ = "dead_letters"
    __table_args__ = (UniqueConstraint("stage", "ref"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stage: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(100))
    error: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
