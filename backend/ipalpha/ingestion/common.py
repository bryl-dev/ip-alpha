import datetime as dt
import hashlib
import json
import logging
import re
import ssl
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import certifi
import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.db.models import Document, Metric, RawEvent

log = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
_JA_CHARS = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")


def http_client(**kwargs: Any) -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        headers={"User-Agent": settings.http_user_agent},
        timeout=settings.http_timeout_s,
        follow_redirects=True,
        **kwargs,
    )


@lru_cache
def ca_bundle_path() -> str:
    """certifi's roots plus, on Windows, the OS store, for clients like curl_cffi that can't
    use truststore (e.g. behind a TLS-inspecting proxy whose root CA is only in Windows)."""
    if sys.platform != "win32":
        return certifi.where()
    pems = [Path(certifi.where()).read_text(encoding="ascii")]
    for store in ("ROOT", "CA"):
        for cert, encoding, _trust in ssl.enum_certificates(store):
            if encoding == "x509_asn":
                pems.append(ssl.DER_cert_to_PEM_cert(cert))
    path = Path(tempfile.gettempdir()) / "ipalpha-ca-bundle.pem"
    path.write_text("\n".join(pems), encoding="ascii")
    return str(path)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def guess_lang(text: str) -> str:
    return "ja" if _JA_CHARS.search(text) else "en"


def store_raw_event(session: Session, source: str, payload: dict[str, Any]) -> bool:
    """Insert a raw payload, deduplicated by content hash. Returns True if it was new."""
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(f"{source}:{body}".encode()).hexdigest()
    stmt = (
        insert(RawEvent)
        .values(source=source, payload=payload, content_hash=digest)
        .on_conflict_do_nothing(index_elements=["content_hash"])
    )
    return session.execute(stmt).rowcount > 0


@dataclass(frozen=True)
class MetricPoint:
    ip_id: int
    source: str
    metric: str
    value: float
    ts: dt.datetime


def upsert_metrics(session: Session, points: Iterable[MetricPoint]) -> int:
    rows = [p.__dict__ for p in points]
    if not rows:
        return 0
    stmt = insert(Metric).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ip_id", "source", "metric", "ts"], set_={"value": stmt.excluded.value}
    )
    session.execute(stmt)
    return len(rows)


@dataclass(frozen=True)
class DocumentIn:
    source: str
    external_id: str
    text: str
    published_at: dt.datetime
    ip_id: int | None = None
    url: str | None = None
    lang: str | None = None


def insert_documents(session: Session, docs: Iterable[DocumentIn]) -> int:
    rows = [
        {
            "source": d.source,
            "external_id": d.external_id,
            "ip_id": d.ip_id,
            "url": d.url,
            "lang": d.lang or guess_lang(d.text),
            "text_original": d.text,
            "published_at": d.published_at,
        }
        for d in docs
    ]
    if not rows:
        return 0
    stmt = insert(Document).values(rows).on_conflict_do_nothing(index_elements=["external_id"])
    return session.execute(stmt).rowcount
