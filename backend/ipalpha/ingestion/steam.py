"""Steam ingestion. SteamDB forbids scraping, so this uses Steam's own public endpoints, with
SteamSpy as a fallback for review counts (the store's appreviews endpoint redirects to the
homepage from some networks)."""

import logging
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ipalpha.db.models import Ip
from ipalpha.ingestion.common import (
    MetricPoint,
    http_client,
    store_raw_event,
    upsert_metrics,
    utcnow,
)

log = logging.getLogger(__name__)

SOURCE = "steam"
CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
STEAMSPY_URL = "https://steamspy.com/api.php"
REQUEST_INTERVAL_S = 1.0


def parse_ccu(payload: dict[str, Any]) -> int | None:
    resp = payload.get("response", {})
    if resp.get("result") != 1:
        return None
    return int(resp["player_count"])


def _review_metrics(total: float, positive: float) -> dict[str, float]:
    return {
        "review_total": float(total),
        "review_positive_ratio": positive / total if total else 0.0,
    }


def parse_review_summary(payload: dict[str, Any]) -> dict[str, float] | None:
    if payload.get("success") != 1:
        return None
    summary = payload["query_summary"]
    return _review_metrics(summary.get("total_reviews") or 0, summary.get("total_positive") or 0)


def parse_steamspy(payload: dict[str, Any]) -> dict[str, float] | None:
    positive, negative = payload.get("positive"), payload.get("negative")
    if positive is None or negative is None:
        return None
    return _review_metrics(positive + negative, positive)


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    resp = client.get(url, params=params)
    resp.raise_for_status()
    if "json" not in resp.headers.get("content-type", ""):
        return None
    return resp.json()


def fetch_ccu(client: httpx.Client, appid: int) -> dict[str, Any] | None:
    return _get_json(client, CCU_URL, {"appid": appid})


def fetch_review_summary(client: httpx.Client, appid: int) -> tuple[str, dict[str, float]] | None:
    """Returns (provider, metrics), trying the Steam store first and SteamSpy second."""
    params = {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0}
    payload = _get_json(client, REVIEWS_URL.format(appid=appid), params)
    if payload and (summary := parse_review_summary(payload)):
        return "steam_store", summary
    time.sleep(REQUEST_INTERVAL_S)
    payload = _get_json(client, STEAMSPY_URL, {"request": "appdetails", "appid": appid})
    if payload and (summary := parse_steamspy(payload)):
        return "steamspy", summary
    return None


def run(session: Session, include_reviews: bool = True, client: httpx.Client | None = None) -> int:
    ips = session.scalars(select(Ip).where(Ip.steam_appid.is_not(None))).all()
    ts = utcnow().replace(minute=0, second=0, microsecond=0)
    points: list[MetricPoint] = []
    with client or http_client() as http:
        for ip in ips:
            appid = ip.steam_appid
            assert appid is not None
            try:
                ccu_payload = fetch_ccu(http, appid)
                ccu = parse_ccu(ccu_payload) if ccu_payload else None
                if ccu is not None:
                    store_raw_event(
                        session,
                        SOURCE,
                        {"kind": "ccu", "appid": appid, "ts": ts.isoformat(), "data": ccu_payload},
                    )
                    points.append(MetricPoint(ip.id, SOURCE, "ccu", float(ccu), ts))

                if include_reviews:
                    time.sleep(REQUEST_INTERVAL_S)
                    if result := fetch_review_summary(http, appid):
                        provider, summary = result
                        store_raw_event(
                            session,
                            SOURCE,
                            {
                                "kind": "reviews",
                                "provider": provider,
                                "appid": appid,
                                "ts": ts.isoformat(),
                                "data": summary,
                            },
                        )
                        points += [MetricPoint(ip.id, SOURCE, k, v, ts) for k, v in summary.items()]
                    else:
                        log.warning("no review data for %s (%s)", ip.slug, appid)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("steam fetch failed for %s (%s): %s", ip.slug, appid, exc)
            time.sleep(REQUEST_INTERVAL_S)
    return upsert_metrics(session, points)
