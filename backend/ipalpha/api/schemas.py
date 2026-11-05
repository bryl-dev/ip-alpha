import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanySummary(BaseModel):
    ticker: str
    name: str
    name_ja: str | None
    currency: str
    ip_count: int
    last_close: float | None
    last_close_date: dt.date | None
    return_5d: float | None
    hype: float | None
    divergence: float | None


class OwnerOut(BaseModel):
    ticker: str
    company_name: str
    role: str
    weight: float
    note: str | None


class IpSummary(BaseModel):
    id: int
    slug: str
    name_en: str
    name_ja: str | None
    type: str
    role: str
    weight: float
    hype: float | None
    hype_date: dt.date | None
    components: dict[str, float]


class CompanyDetail(CompanySummary):
    ips: list[IpSummary]


class SeriesPoint(BaseModel):
    date: dt.date
    close: float | None = None
    hype: float | None = None
    divergence: float | None = None


class MetricOut(BaseModel):
    source: str
    metric: str
    value: float
    ts: dt.datetime


class HypePoint(BaseModel):
    date: dt.date
    score: float
    components: dict[str, float]


class ThesisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    date: dt.date
    divergence: float | None
    body: dict[str, Any]
    model: str
    created_at: dt.datetime


class IpCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    mention_count: int
    example_document_id: int | None
    status: str
    first_seen: dt.datetime
    last_seen: dt.datetime


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class IpDetail(BaseModel):
    id: int
    slug: str
    name_en: str
    name_ja: str | None
    aliases: list[str]
    type: str
    steam_appid: int | None
    mal_id: int | None
    owners: list[OwnerOut]
    latest_metrics: list[MetricOut]
    hype: list[HypePoint]
