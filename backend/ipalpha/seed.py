from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ipalpha.db.models import Company, Ip, IpOwnership, IpType, OwnershipRole


class CompanySeed(BaseModel):
    ticker: str
    name: str
    name_ja: str | None = None
    exchange: str
    currency: str


class OwnerSeed(BaseModel):
    ticker: str
    role: OwnershipRole
    weight: float = Field(ge=0, le=1)
    note: str | None = None


class IpSeed(BaseModel):
    slug: str
    name_en: str
    name_ja: str | None = None
    aliases: list[str] = []
    type: IpType
    steam_appid: int | None = None
    mal_id: int | None = None
    owners: list[OwnerSeed] = Field(min_length=1)


def load_seeds(seeds_dir: Path) -> tuple[list[CompanySeed], list[IpSeed]]:
    companies_raw = yaml.safe_load((seeds_dir / "companies.yaml").read_text(encoding="utf-8"))
    ips_raw = yaml.safe_load((seeds_dir / "ips.yaml").read_text(encoding="utf-8"))
    companies = [CompanySeed.model_validate(c) for c in companies_raw]
    ips = [IpSeed.model_validate(i) for i in ips_raw]

    tickers = {c.ticker for c in companies}
    for ip in ips:
        for owner in ip.owners:
            if owner.ticker not in tickers:
                raise ValueError(f"IP {ip.slug!r} references unknown ticker {owner.ticker!r}")
    return companies, ips


def apply_seeds(session: Session, seeds_dir: Path) -> tuple[int, int]:
    """Upsert companies and IPs; ownership rows for each seeded IP are replaced wholesale."""
    companies, ips = load_seeds(seeds_dir)

    by_ticker: dict[str, Company] = {c.ticker: c for c in session.scalars(select(Company)).all()}
    for seed in companies:
        company = by_ticker.get(seed.ticker) or Company(ticker=seed.ticker)
        company.name, company.name_ja = seed.name, seed.name_ja
        company.exchange, company.currency = seed.exchange, seed.currency
        session.add(company)
        by_ticker[seed.ticker] = company
    session.flush()

    by_slug: dict[str, Ip] = {i.slug: i for i in session.scalars(select(Ip)).all()}
    for ip_seed in ips:
        ip = by_slug.get(ip_seed.slug) or Ip(slug=ip_seed.slug)
        ip.name_en, ip.name_ja, ip.aliases = ip_seed.name_en, ip_seed.name_ja, ip_seed.aliases
        ip.type, ip.steam_appid, ip.mal_id = (
            ip_seed.type.value,
            ip_seed.steam_appid,
            ip_seed.mal_id,
        )
        existing = {(o.company.ticker, o.role): o for o in ip.ownerships}
        wanted = {(o.ticker, o.role.value): o for o in ip_seed.owners}
        for key, stale in existing.items():
            if key not in wanted:
                ip.ownerships.remove(stale)
        for (ticker, role), owner in wanted.items():
            row = existing.get((ticker, role))
            if row is None:
                row = IpOwnership(company=by_ticker[ticker], role=role)
                ip.ownerships.append(row)
            row.weight, row.note = owner.weight, owner.note
        session.add(ip)
    session.flush()
    return len(companies), len(ips)
