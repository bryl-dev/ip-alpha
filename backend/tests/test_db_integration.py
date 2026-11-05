"""Runs the migration, seed, scoring job, and API against a throwaway Postgres database.

Skipped when TEST_DATABASE_URL's server is unreachable.
"""

import datetime as dt
import json
import os
import re
from contextlib import nullcontext

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from fakes import FakeLLM
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ipalpha.agents.analyst import Evidence, ThesisDraft
from ipalpha.agents.mapper import Disambiguation
from ipalpha.agents.normalize import PostAnalysis, TranslatedPost
from ipalpha.agents.pipeline_graph import run_pipeline
from ipalpha.api.main import app
from ipalpha.api.routers import chat as chat_router
from ipalpha.config import REPO_ROOT, get_settings
from ipalpha.db.models import Document, HypeScore, Ip, IpCandidate, Metric, StockPrice, Thesis
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


def _fake_llm() -> FakeLLM:
    def analyze(messages):
        post = messages[-1]["content"]
        mentions = [m for m in ("Erden Ring", "Totally Unknown Game") if m in post]
        fields = dict(ip_mentions=mentions, event_type="dlc_or_update", sentiment=0.8, summary=post)
        if "translate" in post.lower():
            return TranslatedPost(**fields, text_en=f"(en) {post.splitlines()[-1]}")
        return PostAnalysis(**fields)

    def disambiguate(messages):
        typo = '"Erden Ring"' in messages[-1]["content"]
        return Disambiguation(reading="", reason="", choice=1 if typo else 0)

    def thesis(messages):
        first = int(re.search(r"^\[(\d+)\]", messages[-1]["content"], re.M).group(1))
        return ThesisDraft(
            headline="DLC news drives hype",
            stance="hype_ahead_of_price",
            claim="Players returned for the DLC.",
            evidence=[
                Evidence(document_id=first, point="DLC post"),
                Evidence(document_id=999999, point="x"),
            ],
            risks=["Short-lived spike"],
            confidence=0.5,
        )

    def reply(messages):
        first = re.search(r"^\[(\d+)\]", messages[-1]["content"], re.M).group(1)
        return f"Hype is up on DLC news [{first}], unlike [424242]."

    return FakeLLM(
        {
            PostAnalysis: analyze,
            TranslatedPost: analyze,
            Disambiguation: disambiguate,
            ThesisDraft: thesis,
        },
        reply=reply,
    )


def test_agent_pipeline_and_chat(session: Session, monkeypatch):
    elden = session.scalars(select(Ip).where(Ip.slug == "elden-ring")).one()
    when = dt.datetime(2026, 10, 30, 3, tzinfo=dt.UTC)
    session.add_all(
        [
            Document(
                source="reddit",
                external_id="reddit:a",
                text_original="Erden Ring DLC trailer is out and it looks great",
                published_at=when,
            ),
            Document(
                source="bluesky",
                external_id="bluesky:b",
                text_original="新作「Totally Unknown Game」が発表された",
                published_at=when,
            ),
            Document(
                source="reddit",
                external_id="reddit:c:elden-ring",
                ip_id=elden.id,
                text_original="Shadow of the Erdtree tops the charts",
                published_at=when,
            ),
        ]
    )
    session.commit()

    llm = _fake_llm()
    settings = get_settings().model_copy(
        update={"mapper_accept_similarity": 0.99, "mapper_review_similarity": 0.3}
    )
    state = run_pipeline(
        ["normalize", "map_ip", "embed", "score", "analyze"],
        llm=llm,
        settings=settings,
        session_factory=lambda: nullcontext(session),
        tickers=["9468.T"],
        force_analyze=True,
    )
    assert state["errors"] == []
    assert state["results"]["normalize"]["texts"] == 3

    docs = {d.external_id: d for d in session.scalars(select(Document))}
    typo, unknown = docs["reddit:a"], docs["bluesky:b"]
    assert (typo.ip_id, typo.mapping) == (elden.id, "llm")
    assert unknown.lang == "ja" and unknown.text_en.startswith("(en)")
    assert unknown.ip_id is None and unknown.mapping == "unknown"
    assert all(d.embedding is not None for d in docs.values())
    candidate = session.scalars(select(IpCandidate)).one()
    assert candidate.name == "Totally Unknown Game"

    thesis = session.scalars(select(Thesis).where(Thesis.ticker == "9468.T")).one()
    assert len(thesis.body["evidence"]) == 1 and thesis.body["dropped_citations"] == [999999]
    assert thesis.body["disclaimer"]

    monkeypatch.setattr(chat_router, "get_llm", lambda: llm)
    monkeypatch.setattr(chat_router, "session_scope", lambda: nullcontext(session))
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        resp = client.post("/chat", json={"message": "Why is Kadokawa up today?"})
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[len("data: ") :])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        types = [e["type"] for e in events]
        assert types[:2] == ["status", "sources"] and types[-2:] == ["citations", "done"]
        assert "9468.T" in events[0]["text"]
        source_ids = {s["id"] for s in events[1]["sources"]}
        assert typo.id in source_ids
        answer = "".join(e["text"] for e in events if e["type"] == "token")
        assert answer.startswith("Hype is up on DLC news")
        assert events[-2]["invalid"] == [424242] and len(events[-2]["valid"]) == 1

        theses = client.get("/companies/9468.T/theses").json()
        assert theses[0]["body"]["headline"] == "DLC news drives hype"
        names = [c["name"] for c in client.get("/review/ip-candidates").json()]
        assert names == ["Totally Unknown Game"]
    finally:
        app.dependency_overrides.clear()
