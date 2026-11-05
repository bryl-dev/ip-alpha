"""MyAnimeList data. Uses the official MAL API when MAL_CLIENT_ID is set, otherwise Jikan v4
(unofficial; limits: 3 requests/second, 60/minute)."""

import logging
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
from ipalpha.ingestion.http import get_json

log = logging.getLogger(__name__)

SOURCE = "mal"
JIKAN_URL = "https://api.jikan.moe/v4/anime/{mal_id}"
MAL_URL = "https://api.myanimelist.net/v2/anime/{mal_id}"
CACHE_TTL_S = 3600


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
    return get_json(
        client, JIKAN_URL.format(mal_id=mal_id), source="jikan", cache_ttl_s=CACHE_TTL_S
    )


def fetch_mal_official(client: httpx.Client, mal_id: int, client_id: str) -> dict[str, Any]:
    return get_json(
        client,
        MAL_URL.format(mal_id=mal_id),
        source="mal_api",
        params={"fields": "mean,num_list_users"},
        headers={"X-MAL-CLIENT-ID": client_id},
        cache_ttl_s=CACHE_TTL_S,
    )


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
    return upsert_metrics(session, points)
