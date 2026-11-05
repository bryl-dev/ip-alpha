import datetime as dt
import os

import fakeredis
import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ipalpha import llm_events
from ipalpha.config import REPO_ROOT
from ipalpha.db.models import Ip, Metric, StockPrice
from ipalpha.infra import redis as redis_infra
from ipalpha.seed import apply_seeds

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ipalpha:ipalpha@localhost:5433/ipalpha_test"
)
BACKEND = REPO_ROOT / "backend"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(redis_infra, "get_redis", lambda: client)
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _clear_llm_sinks():
    llm_events.clear_sinks()
    yield
    llm_events.clear_sinks()


@pytest.fixture(scope="module")
def engine():
    url = make_url(TEST_DATABASE_URL)
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
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")

    eng = create_engine(TEST_DATABASE_URL)
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
