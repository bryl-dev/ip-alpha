"""Runs the migration, seed, scoring job, and API against a throwaway Postgres database.

Skipped when TEST_DATABASE_URL's server is unreachable.
"""

import datetime as dt
import os

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ipalpha.api.main import app
from ipalpha.config import REPO_ROOT
from ipalpha.db.models import HypeScore, Ip, Metric, StockPrice
from ipalpha.db.session import get_db
from ipalpha.scoring import job as scoring_job
from ipalpha.seed import apply_seeds

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ipalpha:ipalpha@localhost:5433/ipalpha_test"
)
BACKEND = REPO_ROOT / "backend"


@pytest.fixture(scope="module")
def engine():
    url = make_url(TEST_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError:
        pytest.skip("test Postgres is not reachable")
    finally:
        admin.dispose()

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "ipalpha" / "alembic"))
    cfg.attributes["database_url"] = TEST_URL
    command.upgrade(cfg, "head")

    eng = create_engine(TEST_URL)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def session(engine):
    with Session(engine) as s:
        apply_seeds(s, REPO_ROOT / "seeds")
        _insert_synthetic_history(s)
        s.commit()
        yield s


def _insert_synthetic_history(s: Session) -> None:
    """40 days of Elden Ring player counts ending in a spike, and flat-ish prices."""
    elden = s.scalars(select(Ip).where(Ip.slug == "elden-ring")).one()
    end = dt.date(2026, 10, 30)
    rng = np.random.default_rng(42)
    for i in range(40):
        day = end - dt.timedelta(days=39 - i)
        # Two samples within the same JST day (10:00 and 14:00 JST); scoring takes the max.
        for hour in (1, 5):
            ts = dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.UTC)
            value = 20_000 + rng.normal(0, 500) + (60_000 if i == 39 else 0)
            s.add(Metric(ip_id=elden.id, source="steam", metric="ccu", value=value, ts=ts))

    days = [end - dt.timedelta(days=n) for n in range(130)]
    trading = sorted(d for d in days if d.weekday() < 5)
    for n, d in enumerate(trading):
        bench = 38_000 * (1 + 0.0005 * n)
        stock = 3_500 * (1 + 0.0005 * n) * (1 + rng.normal(0, 0.004))
        s.add(StockPrice(ticker="^N225", date=d, close=bench))
        s.add(StockPrice(ticker="9468.T", date=d, close=stock))
    s.flush()


def test_scoring_job_writes_ip_and_company_rows(session: Session):
    result = scoring_job.run(session)
    session.commit()
    assert result["ip_rows"] > 0 and result["company_rows"] > 0

    latest = session.scalars(
        select(HypeScore).where(HypeScore.company_id.is_not(None)).order_by(HypeScore.date.desc())
    ).first()
    assert latest is not None
    assert latest.date == dt.date(2026, 10, 30)
    assert latest.score > 0
    # Hype spiked while the stock tracked the market, so divergence should be positive.
    assert latest.divergence is not None and latest.divergence > 0

    # Re-running replaces rows rather than duplicating them.
    before = session.scalar(select(func.count()).select_from(HypeScore))
    scoring_job.run(session)
    session.commit()
    assert session.scalar(select(func.count()).select_from(HypeScore)) == before


def test_api_serves_scored_company(session: Session):
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        companies = {c["ticker"]: c for c in client.get("/companies").json()}
        assert companies["9468.T"]["hype"] is not None
        assert companies["7974.T"]["hype"] is None

        detail = client.get("/companies/9468.T").json()
        assert detail["ips"][0]["slug"] == "elden-ring"  # sorted by hype, scored first
        assert "ccu" in detail["ips"][0]["components"]

        series = client.get("/companies/9468.T/series?from=2026-09-01&to=2026-10-30").json()
        assert series[-1]["date"] == "2026-10-30"
        assert series[-1]["close"] is not None and series[-1]["divergence"] is not None

        assert client.get("/companies/NOPE").status_code == 404
    finally:
        app.dependency_overrides.clear()
