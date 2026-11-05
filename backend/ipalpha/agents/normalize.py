"""Translation and Normalization Agent: detects the language of each stored post, translates
non-English text with a fandom-slang glossary, and extracts titles, event type, sentiment, and a
one-line summary as validated JSON."""

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ipalpha.agents.common import dead_letter, not_dead_lettered
from ipalpha.agents.language import detect_language
from ipalpha.config import Settings
from ipalpha.db.models import Document
from ipalpha.ingestion.common import utcnow
from ipalpha.llm import LLM, Completion, Message, StructuredOutputError

log = logging.getLogger(__name__)

STAGE = "normalize"
MAX_INPUT_CHARS = 2000

EventType = Literal[
    "anime_adaptation",
    "sequel_or_new_season",
    "release",
    "dlc_or_update",
    "announcement",
    "sales_or_ranking",
    "review_or_reaction",
    "controversy",
    "discussion",
    "other",
]


class PostAnalysis(BaseModel):
    ip_mentions: list[str] = Field(
        description="Titles of games, anime, manga or novels, exactly as written in the post"
    )
    event_type: EventType
    sentiment: float = Field(ge=-1, le=1)
    summary: str = Field(description="One English sentence, under 30 words")


class TranslatedPost(PostAnalysis):
    text_en: str = Field(description="Faithful English translation of the whole post")


GLOSSARY = """\
- アニメ化 (anime-ka): an anime adaptation was announced
- 続編決定, 第2期 / 第N期: a sequel or new season was confirmed
- 神ゲー (kami-ge): masterpiece game. クソゲー (kusoge): terrible game
- 神回 (kami-kai): outstanding episode. 作画崩壊 (sakuga houkai): animation quality collapsed
- 覇権 (haken): the dominant hit of the season
- 爆売れ: selling extremely well. 爆死 (bakushi): a commercial flop, except in gacha talk where \
it means the poster spent a lot and got nothing (personal bad luck, not a verdict on the title)
- 重版 / 重版出来: a reprint was ordered because copies sold out (strong sales)
- 炎上 (enjou): online backlash or controversy
- ガチャ / gacha: paid random draws in a game
- 推し (oshi): the poster's favourite character or creator
- isekai, moe, waifu: genre or fandom terms; on their own they carry no sentiment"""

SYSTEM_PROMPT = f"""You analyze fan posts about Japanese games, anime, manga, and light novels \
for a market-research tool. Return JSON only.

Fields:
- ip_mentions: titles of games, anime, manga, or novels named in the post, copied exactly as \
written (keep the original script; do not translate them). Leave out company, studio, and \
character names. Use an empty list if no title is named.
- event_type: the single best category for what the post is about.
- sentiment: from -1 (very negative) to 1 (very positive) toward the titles mentioned; 0 when \
neutral or unclear.
- summary: one English sentence under 30 words.
- text_en (only when asked to translate): a faithful English translation that keeps titles as \
written and explains slang in plain English.

Fandom glossary:
{GLOSSARY}"""

_EXAMPLE_POST = "「薬屋のひとりごと」第3期決定！作画も神回続きだったし覇権確定でしょ"
_EXAMPLE_ANSWER = TranslatedPost(
    text_en="A third season of '薬屋のひとりごと' has been confirmed! The animation had one "
    "outstanding episode after another, so it's surely going to be the season's top hit.",
    ip_mentions=["薬屋のひとりごと"],
    event_type="sequel_or_new_season",
    sentiment=0.9,
    summary="Fan celebrates the confirmed third season of 薬屋のひとりごと and expects it to be "
    "the season's biggest hit.",
)


def _task(lang: str) -> str:
    if lang == "en":
        return "Analyze this post."
    return f"Translate and analyze this post (detected language: {lang})."


def build_messages(text: str, lang: str) -> list[Message]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{_task('ja')}\n\n{_EXAMPLE_POST}"},
        {"role": "assistant", "content": _EXAMPLE_ANSWER.model_dump_json()},
        {"role": "user", "content": f"{_task(lang)}\n\n{text[:MAX_INPUT_CHARS]}"},
    ]


def normalize_text(llm: LLM, text: str, lang: str) -> Completion[PostAnalysis]:
    schema = PostAnalysis if lang == "en" else TranslatedPost
    return llm.structured(build_messages(text, lang), schema)


def run(session: Session, llm: LLM, settings: Settings) -> dict[str, int]:
    rows = session.execute(
        select(Document.id, Document.text_original)
        .where(Document.normalized_at.is_(None), not_dead_lettered(STAGE))
        .order_by(Document.published_at.desc())
        .limit(settings.normalize_batch_size * 3)
    ).all()
    # The same post is stored once per matched IP; analyze each distinct text once.
    by_text: dict[str, list[int]] = {}
    for doc_id, text in rows:
        if text in by_text or len(by_text) < settings.normalize_batch_size:
            by_text.setdefault(text, []).append(doc_id)

    stats = {"texts": 0, "documents": 0, "failed": 0, "tokens": 0}
    for text, ids in by_text.items():
        lang = detect_language(text)
        try:
            result = normalize_text(llm, text, lang)
        except StructuredOutputError as exc:
            for doc_id in ids:
                dead_letter(session, STAGE, f"document:{doc_id}", str(exc))
            session.commit()
            stats["failed"] += 1
            continue
        value = result.value
        text_en = value.text_en if isinstance(value, TranslatedPost) else text
        updated = session.execute(
            update(Document)
            .where(Document.text_original == text, Document.normalized_at.is_(None))
            .values(
                lang=lang,
                text_en=text_en,
                sentiment=value.sentiment,
                event_type=value.event_type,
                summary=value.summary,
                ip_mentions=[m.strip() for m in value.ip_mentions if m.strip()][:10],
                normalized_at=utcnow(),
            )
        )
        session.commit()
        stats["texts"] += 1
        stats["documents"] += updated.rowcount
        stats["tokens"] += result.tokens
    log.info("normalize: %s", json.dumps(stats))
    return stats
