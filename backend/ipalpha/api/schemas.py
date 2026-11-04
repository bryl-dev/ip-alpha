import datetime as dt

from pydantic import BaseModel


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
