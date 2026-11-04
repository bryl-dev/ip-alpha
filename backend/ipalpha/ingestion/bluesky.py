import datetime as dt
import logging
import time

from atproto import Client
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.ingestion.common import DocumentIn, insert_documents, utcnow
from ipalpha.ingestion.matching import IpMatcher
from ipalpha.ingestion.social import documents_for_post

log = logging.getLogger(__name__)

SOURCE = "bluesky"
REQUEST_INTERVAL_S = 1.0
LOOKBACK = dt.timedelta(days=1)


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def run(session: Session) -> int:
    settings = get_settings()
    if not (settings.bluesky_handle and settings.bluesky_app_password):
        log.warning("bluesky credentials not set (BLUESKY_HANDLE/APP_PASSWORD); skipping")
        return 0

    client = Client()
    client.login(settings.bluesky_handle, settings.bluesky_app_password)
    matcher = IpMatcher.from_db(session)
    since = (utcnow() - LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")

    docs: list[DocumentIn] = []
    for ip in matcher.ips:
        try:
            resp = client.app.bsky.feed.search_posts(
                params={"q": f'"{ip.name_en}"', "limit": 100, "since": since}
            )
        except Exception as exc:
            log.warning("bluesky search failed for %s: %s", ip.slug, exc)
            continue
        for post in resp.posts:
            text = getattr(post.record, "text", "") or ""
            # Search is fuzzy, so re-check with the alias matcher before counting a mention.
            if not text or ip not in matcher.match(text):
                continue
            rkey = post.uri.rsplit("/", 1)[-1]
            langs = getattr(post.record, "langs", None) or []
            docs += documents_for_post(
                matcher,
                source=SOURCE,
                post_id=rkey,
                text=text,
                published_at=_parse_ts(post.record.created_at),
                url=f"https://bsky.app/profile/{post.author.handle}/post/{rkey}",
                lang=langs[0][:8] if langs else None,
            )
        time.sleep(REQUEST_INTERVAL_S)
    return insert_documents(session, docs)
