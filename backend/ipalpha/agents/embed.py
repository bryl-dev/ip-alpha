"""Embeds normalized documents with bge-m3 so RAG can search them."""

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ipalpha.agents.mapper import embed_all
from ipalpha.config import Settings
from ipalpha.db.models import Document
from ipalpha.llm import LLM

log = logging.getLogger(__name__)

MAX_CHARS = 2000


def embedding_text(text_en: str | None, text_original: str) -> str:
    return (text_en or text_original)[:MAX_CHARS]


def run(session: Session, llm: LLM, settings: Settings) -> dict[str, int]:
    rows = session.execute(
        select(Document.id, Document.text_en, Document.text_original)
        .where(Document.embedding.is_(None), Document.normalized_at.is_not(None))
        .order_by(Document.published_at.desc())
        .limit(settings.normalize_batch_size * 5)
    ).all()
    by_text: dict[str, list[int]] = {}
    for doc_id, text_en, text_original in rows:
        by_text.setdefault(embedding_text(text_en, text_original), []).append(doc_id)
    texts = list(by_text)
    for text, vector in zip(texts, embed_all(llm, texts), strict=True):
        session.execute(
            update(Document).where(Document.id.in_(by_text[text])).values(embedding=vector)
        )
    session.commit()
    log.info("embed: %d texts, %d documents", len(texts), len(rows))
    return {"texts": len(texts), "documents": len(rows)}
