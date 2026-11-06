"""Hallucination checks for cited document ids: existence, shown-set membership, and relevance."""

import re
from collections.abc import Collection, Sequence

from pydantic import BaseModel, computed_field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ipalpha.db.models import Company, Document, IpOwnership

_EN_SUFFIX = re.compile(r",?\s+(Co\.,?\s*Ltd\.|Corporation|Holdings|Group|Inc\.)", re.IGNORECASE)
_JA_SUFFIX = re.compile(r"(グループ|ホールディングス)$")


class CitationAudit(BaseModel):
    cited: list[int]
    valid: list[int]
    unknown: list[int]
    missing: list[int]
    unrelated: list[int]

    @computed_field
    def failure_rate(self) -> float:
        return (len(self.cited) - len(self.valid)) / len(self.cited) if self.cited else 0.0


def company_terms(company: Company) -> set[str]:
    terms = {company.name, _EN_SUFFIX.sub("", company.name).strip(), company.ticker}
    terms.add(company.ticker.split(".")[0])
    if company.name_ja:
        terms |= {company.name_ja, _JA_SUFFIX.sub("", company.name_ja)}
    return {t for t in terms if t}


def _mentions(text: str, term: str) -> bool:
    if not term.isascii():
        return term in text
    pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _unique(ids: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(ids))


def _doc_text(doc: Document) -> str:
    return "\n".join(part for part in (doc.text_original, doc.text_en, doc.summary) if part)


def _relevant(doc: Document, owned_ip_ids: set[int], terms: set[str]) -> bool:
    if doc.ip_id is not None and doc.ip_id in owned_ip_ids:
        return True
    text = _doc_text(doc)
    return any(_mentions(text, term) for term in terms)


def audit_citations(
    session: Session,
    cited: Sequence[int],
    shown_ids: Collection[int],
    tickers: Sequence[str],
) -> CitationAudit:
    cited_ids = _unique(cited)
    shown = set(shown_ids)
    unknown = [i for i in cited_ids if i not in shown]
    in_shown = [i for i in cited_ids if i in shown]

    docs = (
        {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(in_shown)))}
        if in_shown
        else {}
    )
    missing = [i for i in in_shown if i not in docs]
    existing = [i for i in in_shown if i in docs]

    if not tickers:
        return CitationAudit(
            cited=cited_ids, valid=existing, unknown=unknown, missing=missing, unrelated=[]
        )

    companies = list(
        session.scalars(
            select(Company)
            .where(Company.ticker.in_(list(tickers)))
            .options(selectinload(Company.ownerships).selectinload(IpOwnership.ip))
        )
    )
    owned_ips = [o.ip for c in companies for o in c.ownerships if o.ip is not None]
    owned_ip_ids = {ip.id for ip in owned_ips}
    terms: set[str] = set()
    for company in companies:
        terms |= company_terms(company)
    for ip in owned_ips:
        terms.add(ip.name_en)
        if ip.name_ja:
            terms.add(ip.name_ja)
        terms.update(ip.aliases or [])

    valid, unrelated = [], []
    for doc_id in existing:
        if _relevant(docs[doc_id], owned_ip_ids, terms):
            valid.append(doc_id)
        else:
            unrelated.append(doc_id)
    return CitationAudit(
        cited=cited_ids, valid=valid, unknown=unknown, missing=missing, unrelated=unrelated
    )
