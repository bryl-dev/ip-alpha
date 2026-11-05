"""IP-to-Equity Mapper: ties posts that matched no tracked IP at ingestion to one, in three steps:
alias match, embedding similarity, then LLM disambiguation among the closest candidates. It never
invents an owner; titles it can't resolve go to the ip_candidates review queue."""

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ipalpha.config import Settings
from ipalpha.db.models import Document, Ip, IpCandidate
from ipalpha.ingestion.matching import IpMatcher, IpRef
from ipalpha.llm import LLM, StructuredOutputError

log = logging.getLogger(__name__)

EMBED_BATCH = 64
TOP_K = 3
CONTEXT_CHARS = 600

Method = Literal["alias", "embedding", "llm"]


@dataclass(frozen=True)
class Candidate:
    ip: IpRef
    similarity: float


class Disambiguation(BaseModel):
    # Small models choose better when they read the title and reason before committing.
    reading: str = Field(description="The mentioned title in English or romaji")
    reason: str
    choice: int = Field(ge=0, description="Number of the matching title, or 0 if none match")


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def embed_all(llm: LLM, texts: Sequence[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        out += llm.embed(texts[i : i + EMBED_BATCH])
    return out


class TermIndex:
    """One vector per name or alias of every tracked IP; an IP's similarity is its best term's."""

    def __init__(self, ips: Sequence[IpRef], vectors: Sequence[Sequence[float]]):
        self.owners = [ip for ip in ips for _ in ip.terms]
        self.vectors = _unit(np.asarray(vectors, dtype=float))

    @classmethod
    def build(cls, ips: Sequence[IpRef], llm: LLM) -> "TermIndex":
        return cls(ips, embed_all(llm, [t for ip in ips for t in ip.terms]))

    def candidates(self, vector: Sequence[float], k: int = TOP_K) -> list[Candidate]:
        sims = self.vectors @ _unit(np.asarray(vector, dtype=float))
        best: dict[int, Candidate] = {}
        for ip, sim in zip(self.owners, sims, strict=True):
            if ip.id not in best or sim > best[ip.id].similarity:
                best[ip.id] = Candidate(ip, float(sim))
        return sorted(best.values(), key=lambda c: -c.similarity)[:k]


def triage(
    candidates: list[Candidate], accept: float, review: float
) -> tuple[Literal["accept", "ask", "reject"], list[Candidate]]:
    shortlist = [c for c in candidates if c.similarity >= review]
    if not shortlist:
        return "reject", []
    if shortlist[0].similarity >= accept:
        return "accept", shortlist[:1]
    return "ask", shortlist


def disambiguate(
    llm: LLM, mention: str, context: str, shortlist: list[Candidate], labels: dict[int, str]
) -> IpRef | None:
    options = "\n".join(f"{i}. {labels[c.ip.id]}" for i, c in enumerate(shortlist, 1))
    messages = [
        {
            "role": "system",
            "content": "You match title mentions in fan posts to a fixed list of tracked "
            "entertainment titles. First write the mentioned title in English or romaji, then "
            "compare it with each option's names and aliases. Pick a number only if the mention "
            "clearly refers to that title, including its sequels, spin-offs, DLC, or "
            "adaptations. If none fits, or you are unsure, answer 0.",
        },
        {
            "role": "user",
            "content": f'Mention: "{mention}"\nPost: {context[:CONTEXT_CHARS]}\n\n'
            f"Tracked titles:\n{options}\n0. None of these",
        },
    ]
    try:
        choice = llm.structured(messages, Disambiguation).value.choice
    except StructuredOutputError as exc:
        log.warning("disambiguation failed for %r: %s", mention, exc)
        return None
    return shortlist[choice - 1].ip if 1 <= choice <= len(shortlist) else None


def _candidate_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()[:200]


def _record_candidate(session: Session, name: str, document_id: int) -> None:
    stmt = insert(IpCandidate).values(
        key=_candidate_key(name), name=name[:200], example_document_id=document_id
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"mention_count": IpCandidate.mention_count + 1, "last_seen": func.now()},
    )
    session.execute(stmt)


def _labels(session: Session) -> dict[int, str]:
    labels = {}
    for ip in session.scalars(select(Ip)):
        ja = f" / {ip.name_ja}" if ip.name_ja else ""
        aliases = [a for a in ip.aliases or [] if a not in (ip.name_en, ip.name_ja)]
        also = f"; also called {', '.join(aliases)}" if aliases else ""
        labels[ip.id] = f"{ip.name_en}{ja} ({ip.type.replace('_', ' ')}{also})"
    return labels


def _copy_for_ip(doc: Document, ip: IpRef, method: str) -> dict:
    return {
        "source": doc.source,
        "external_id": f"{doc.external_id}:{ip.slug}"[:300],
        "ip_id": ip.id,
        "url": doc.url,
        "lang": doc.lang,
        "text_original": doc.text_original,
        "text_en": doc.text_en,
        "sentiment": doc.sentiment,
        "published_at": doc.published_at,
        "event_type": doc.event_type,
        "summary": doc.summary,
        "ip_mentions": doc.ip_mentions,
        "normalized_at": doc.normalized_at,
        "mapping": method,
    }


def run(session: Session, llm: LLM, settings: Settings) -> dict[str, int]:
    docs = session.scalars(
        select(Document)
        .where(
            Document.ip_id.is_(None),
            Document.normalized_at.is_not(None),
            Document.mapping.is_(None),
        )
        .order_by(Document.published_at.desc())
        .limit(settings.normalize_batch_size * 3)
    ).all()
    stats = {"documents": 0, "alias": 0, "embedding": 0, "llm": 0, "unknown": 0, "none": 0}
    if not docs:
        return stats

    matcher = IpMatcher.from_db(session)
    index = TermIndex.build(matcher.ips, llm)
    labels = _labels(session)
    decided: dict[str, tuple[IpRef, Method] | None] = {}

    def resolve(mention: str, context: str, vector: Sequence[float]) -> tuple[IpRef, Method] | None:
        key = _candidate_key(mention)
        if key not in decided:
            verdict, shortlist = triage(
                index.candidates(vector),
                settings.mapper_accept_similarity,
                settings.mapper_review_similarity,
            )
            if verdict == "accept":
                decided[key] = (shortlist[0].ip, "embedding")
            elif verdict == "ask":
                ip = disambiguate(llm, mention, context, shortlist, labels)
                decided[key] = (ip, "llm") if ip else None
            else:
                decided[key] = None
        return decided[key]

    pending = sorted(
        {m for d in docs for m in (d.ip_mentions or []) if len(m) >= 2 and not matcher.match(m)}
    )
    vectors = dict(zip(pending, embed_all(llm, pending), strict=True))

    for doc in docs:
        found: dict[int, tuple[IpRef, str]] = {}
        for ip in matcher.match(doc.text_en or ""):
            found.setdefault(ip.id, (ip, "alias"))
        for mention in doc.ip_mentions or []:
            hits = matcher.match(mention)
            for ip in hits:
                found.setdefault(ip.id, (ip, "alias"))
            if hits or mention not in vectors:
                continue
            match = resolve(mention, doc.text_en or doc.text_original, vectors[mention])
            if match:
                found.setdefault(match[0].id, match)
            else:
                _record_candidate(session, mention, doc.id)

        stats["documents"] += 1
        if not found:
            doc.mapping = "unknown" if doc.ip_mentions else "none"
            stats[doc.mapping] += 1
            continue
        (ip, method), *others = found.values()
        doc.ip_id, doc.mapping = ip.id, method
        stats[method] += 1
        for other, other_method in others:
            session.execute(
                insert(Document)
                .values(_copy_for_ip(doc, other, other_method))
                .on_conflict_do_nothing(index_elements=["external_id"])
            )
    session.commit()
    log.info("map_ip: %s", json.dumps(stats))
    return stats
