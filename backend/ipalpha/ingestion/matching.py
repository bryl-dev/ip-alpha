"""Deterministic alias matching of free text to tracked IPs (the Phase 2 mapper builds on this)."""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ipalpha.db.models import Ip

# Short ASCII aliases such as "SAO" or "RE4" collide with ordinary words when matched
# case-insensitively, so they must match exactly.
_CASE_SENSITIVE_MAX_LEN = 4


@dataclass(frozen=True)
class IpRef:
    id: int
    slug: str
    name_en: str
    terms: tuple[str, ...]


def _term_pattern(term: str) -> str:
    escaped = re.escape(term)
    if term.isascii():
        escaped = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    if term.isascii() and len(term) <= _CASE_SENSITIVE_MAX_LEN:
        return f"(?-i:{escaped})"
    return escaped


class IpMatcher:
    def __init__(self, ips: list[IpRef]):
        self.ips = ips
        self._patterns = [
            (ip, re.compile("|".join(_term_pattern(t) for t in ip.terms), re.IGNORECASE))
            for ip in ips
            if ip.terms
        ]

    @classmethod
    def from_db(cls, session: Session) -> "IpMatcher":
        refs = []
        for ip in session.scalars(select(Ip).order_by(Ip.id)):
            terms = {ip.name_en, *(ip.aliases or [])}
            if ip.name_ja:
                terms.add(ip.name_ja)
            refs.append(IpRef(ip.id, ip.slug, ip.name_en, tuple(sorted(t for t in terms if t))))
        return cls(refs)

    def match(self, text: str) -> list[IpRef]:
        return [ip for ip, pattern in self._patterns if pattern.search(text)]
