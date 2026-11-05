from sqlalchemy import exists, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ipalpha.db.models import DeadLetter, Document


def dead_letter(session: Session, stage: str, ref: str, error: str) -> None:
    stmt = insert(DeadLetter).values(stage=stage, ref=ref, error=error[:4000])
    stmt = stmt.on_conflict_do_update(
        index_elements=["stage", "ref"],
        set_={
            "error": stmt.excluded.error,
            "attempts": DeadLetter.attempts + 1,
            "updated_at": func.now(),
        },
    )
    session.execute(stmt)


def not_dead_lettered(stage: str) -> ColumnElement[bool]:
    return ~exists().where(
        DeadLetter.stage == stage,
        DeadLetter.ref == func.concat("document:", Document.id),
    )
