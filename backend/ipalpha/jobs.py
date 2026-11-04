import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from ipalpha.db.session import session_scope
from ipalpha.ingestion import bluesky, finance, jikan, oricon, reddit, steam
from ipalpha.scoring import job as scoring_job

log = logging.getLogger(__name__)

JOBS: dict[str, Callable[[Session], object]] = {
    "steam_ccu": lambda s: steam.run(s, include_reviews=False),
    "steam": steam.run,
    "mal": jikan.run,
    "oricon": oricon.run,
    "reddit": reddit.run,
    "bluesky": bluesky.run,
    "prices": finance.run,
    "score": scoring_job.run,
}

DAILY_INGEST = ["steam", "mal", "oricon", "reddit", "bluesky", "prices"]


def run_job(name: str) -> object:
    """Each job gets its own transaction so one failing source doesn't roll back the others."""
    try:
        with session_scope() as session:
            result = JOBS[name](session)
        log.info("job %s finished: %s", name, result)
        return result
    except Exception:
        log.exception("job %s failed", name)
        return None
