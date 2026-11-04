"""MyAnimeList data. Uses the official MAL API when MAL_CLIENT_ID is set, otherwise Jikan v4
(unofficial; limits: 3 requests/second, 60/minute)."""

import logging
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.db.models import Ip
from ipalpha.ingestion.common import (
    MetricPoint,
    http_client,
    store_raw_event,
    upsert_metrics,
    utcnow,
)

log = logging.getLogger(__name__)

SOURCE = "mal"
JIKAN_URL = "https://api.jikan.moe/v4/anime/{mal_id}"
MAL_URL = "https://api.myanimelist.net/v2/anime/{mal_id}"
REQUEST_INTERVAL_S = 1.1
RETRY_AFTER_429_S = 5.0


def parse_anime(payload: dict[str, Any]) -> dict[str, float]:
    """Jikan response."""
    data = payload["data"]
    out: dict[str, float] = {}
    for key, metric in (
        ("score", "mal_score"),
        ("members", "mal_members"),
        ("favorites", "mal_favorites"),
    ):
        if data.get(key) is not None:
            out[metric] = float(data[key])
    return out


def parse_mal_official(payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, metric in (("mean", "mal_score"), ("num_list_users", "mal_members")):
        if payload.get(key) is not None:
            out[metric] = float(payload[key])
    return out


def fetch_anime(client: httpx.Client, mal_id: int) -> dict[str, Any]:
    for attempt in range(3):
        resp = client.get(JIKAN_URL.format(mal_id=mal_id))
        if resp.status_code == 429 and attempt < 2:
            time.sleep(RETRY_AFTER_429_S * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise AssertionError("unreachable")


def fetch_mal_official(client: httpx.Client, mal_id: int, client_id: str) -> dict[str, Any]:
    resp = client.get(
        MAL_URL.format(mal_id=mal_id),
        params={"fields": "mean,num_list_users"},
        headers={"X-MAL-CLIENT-ID": client_id},
    )
    resp.raise_for_status()
    return resp.json()


def run(session: Session, client: httpx.Client | None = None) -> int:
    client_id = get_settings().mal_client_id
    provider = "mal_api" if client_id else "jikan"
    ips = session.scalars(select(Ip).where(Ip.mal_id.is_not(None))).all()
    ts = utcnow().replace(minute=0, second=0, microsecond=0)
    points: list[MetricPoint] = []
    with client or http_client() as http:
        for ip in ips:
            assert ip.mal_id is not None
            try:
                if client_id:
                    stats = parse_mal_official(fetch_mal_official(http, ip.mal_id, client_id))
                else:
                    stats = parse_anime(fetch_anime(http, ip.mal_id))
                store_raw_event(
                    session,
                    SOURCE,
                    {
                        "provider": provider,
                        "mal_id": ip.mal_id,
                        "ts": ts.isoformat(),
                        "data": stats,
                    },
                )
                points += [MetricPoint(ip.id, SOURCE, k, v, ts) for k, v in stats.items()]
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                log.warning("%s unreachable (%s); skipping the remaining IPs", provider, exc)
                break
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("%s fetch failed for %s (%s): %s", provider, ip.slug, ip.mal_id, exc)
            time.sleep(REQUEST_INTERVAL_S)
    return upsert_metrics(session, points)
