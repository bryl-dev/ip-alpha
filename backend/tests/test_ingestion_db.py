"""Ingestion writes against the test Postgres; skipped when it is unreachable."""

import datetime as dt

from sqlalchemy.orm import Session

from ipalpha.ingestion.common import DocumentIn, insert_documents


def test_insert_documents_returns_inserted_row_count(session: Session):
    when = dt.datetime(2026, 10, 30, 3, tzinfo=dt.UTC)
    docs = [DocumentIn("reddit", f"reddit:rowcount-{n}", f"post {n}", when) for n in range(2)]
    assert insert_documents(session, docs) == 2
    assert insert_documents(session, docs) == 0
    extra = DocumentIn("reddit", "reddit:rowcount-2", "post 2", when)
    assert insert_documents(session, [*docs, extra]) == 1
    assert insert_documents(session, []) == 0
    session.rollback()
