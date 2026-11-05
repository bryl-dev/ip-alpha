"""Oricon weekly book ranking scraper.

This is the most fragile source: it depends on Oricon's HTML. The parser is kept separate from
fetching and is covered by a fixture-based test, so a layout change shows up as a test failure.
"""

import datetime as dt
import logging
import re
from dataclasses import asdict, dataclass

import httpx
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.ingestion.common import (
    JST,
    MetricPoint,
    http_client,
    store_raw_event,
    upsert_metrics,
)
from ipalpha.ingestion.http import get_text
from ipalpha.ingestion.matching import IpMatcher

log = logging.getLogger(__name__)

SOURCE = "oricon"
CACHE_TTL_S = 21600
_DATE_RANGE = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[~〜～]\s*(\d{4})年(\d{1,2})月(\d{1,2})日"
)
_UNITS = re.compile(r"推定売上部数[:：]\s*([\d,]+)")
_PUBLISHER = re.compile(r"出版社[:：]\s*(.+)")


class OriconParseError(ValueError):
    pass


@dataclass(frozen=True)
class RankEntry:
    rank: int
    title: str
    author: str | None
    publisher: str | None
    units: int | None


@dataclass(frozen=True)
class Ranking:
    week_start: dt.date
    week_end: dt.date
    entries: list[RankEntry]


def parse_ranking(html: str) -> Ranking:
    tree = LexborHTMLParser(html)
    date_node = tree.css_first("header.block-ranking-header p.date")
    match = _DATE_RANGE.search(date_node.text() if date_node else "")
    if not match:
        raise OriconParseError("could not find the ranking week in the page header")
    y1, m1, d1, y2, m2, d2 = map(int, match.groups())

    entries = []
    for section in tree.css("section.box-rank-entry"):
        num = section.css_first("p.num")
        title = section.css_first("h2.title")
        if not num or not title or not num.text(strip=True).isdigit():
            continue
        author = section.css_first("p.name")
        publisher = units = None
        for li in section.css("ul.list li"):
            text = li.text(strip=True)
            if m := _UNITS.search(text):
                units = int(m.group(1).replace(",", ""))
            elif m := _PUBLISHER.search(text):
                publisher = m.group(1).strip()
        entries.append(
            RankEntry(
                rank=int(num.text(strip=True)),
                title=title.text(strip=True),
                author=author.text(strip=True) if author else None,
                publisher=publisher,
                units=units,
            )
        )
    if not entries:
        raise OriconParseError("no ranking entries found; the page layout may have changed")
    return Ranking(dt.date(y1, m1, d1), dt.date(y2, m2, d2), entries)


def units_by_ip(ranking: Ranking, matcher: IpMatcher) -> dict[int, int]:
    totals: dict[int, int] = {}
    for entry in ranking.entries:
        if entry.units is None:
            continue
        for ip in matcher.match(entry.title):
            totals[ip.id] = totals.get(ip.id, 0) + entry.units
    return totals


def run(session: Session, client: httpx.Client | None = None) -> int:
    url = get_settings().oricon_ranking_url
    with client or http_client() as http:
        html = get_text(http, url, source=SOURCE, cache_ttl_s=CACHE_TTL_S)
    ranking = parse_ranking(html)
    store_raw_event(
        session,
        SOURCE,
        {
            "url": url,
            "week_start": ranking.week_start.isoformat(),
            "week_end": ranking.week_end.isoformat(),
            "entries": [asdict(e) for e in ranking.entries],
        },
    )
    ts = dt.datetime.combine(ranking.week_end, dt.time(), tzinfo=JST)
    totals = units_by_ip(ranking, IpMatcher.from_db(session))
    log.info(
        "oricon week ending %s: %d entries, %d tracked IPs charted",
        ranking.week_end,
        len(ranking.entries),
        len(totals),
    )
    return upsert_metrics(
        session,
        [MetricPoint(ip_id, SOURCE, "oricon_units", float(u), ts) for ip_id, u in totals.items()],
    )
