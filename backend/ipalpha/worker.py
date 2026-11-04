"""Phase 1 scheduler. Phase 3 replaces this with arq workers backed by Redis."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ipalpha.jobs import run_job

log = logging.getLogger(__name__)
TZ = "Asia/Tokyo"


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=TZ)
    scheduler.add_job(
        run_job,
        CronTrigger(minute=5, timezone=TZ),
        args=["steam_ccu"],
        id="steam_ccu",
        max_instances=1,
        coalesce=True,
    )
    # TSE closes at 15:30 JST; pull prices after that and everything else in the early morning.
    daily = {
        "steam": "6:10",
        "mal": "6:20",
        "oricon": "6:30",
        "reddit": "6:40",
        "bluesky": "6:50",
        "prices": "16:30",
        "score": "17:00",
    }
    for job, at in daily.items():
        hour, minute = map(int, at.split(":"))
        scheduler.add_job(
            run_job,
            CronTrigger(hour=hour, minute=minute, timezone=TZ),
            args=[job],
            id=job,
            max_instances=1,
            coalesce=True,
        )
    return scheduler


def run_scheduler() -> None:
    scheduler = build_scheduler()
    for job in scheduler.get_jobs():
        log.info("scheduled %s: %s", job.id, job.trigger)
    scheduler.start()
