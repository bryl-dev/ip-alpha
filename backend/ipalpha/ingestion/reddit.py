import datetime as dt
import logging

import praw
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.ingestion.common import DocumentIn, insert_documents
from ipalpha.ingestion.matching import IpMatcher
from ipalpha.ingestion.social import documents_for_post

log = logging.getLogger(__name__)

SOURCE = "reddit"


def run(session: Session) -> int:
    settings = get_settings()
    if not (settings.reddit_client_id and settings.reddit_client_secret):
        log.warning("reddit credentials not set (REDDIT_CLIENT_ID/SECRET); skipping")
        return 0

    reddit = praw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        check_for_async=False,
    )
    reddit.read_only = True
    matcher = IpMatcher.from_db(session)

    docs: list[DocumentIn] = []
    for name in settings.reddit_subreddits:
        try:
            for post in reddit.subreddit(name).new(limit=settings.reddit_posts_per_subreddit):
                text = f"{post.title}\n\n{post.selftext or ''}".strip()
                docs += documents_for_post(
                    matcher,
                    source=SOURCE,
                    post_id=post.id,
                    text=text,
                    published_at=dt.datetime.fromtimestamp(post.created_utc, dt.UTC),
                    url=f"https://www.reddit.com{post.permalink}",
                )
        except Exception as exc:  # praw raises a wide range of exception types
            log.warning("reddit fetch failed for r/%s: %s", name, exc)
    return insert_documents(session, docs)
