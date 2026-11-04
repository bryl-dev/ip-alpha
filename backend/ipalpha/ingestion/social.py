from ipalpha.ingestion.common import DocumentIn
from ipalpha.ingestion.matching import IpMatcher


def documents_for_post(
    matcher: IpMatcher,
    *,
    source: str,
    post_id: str,
    text: str,
    published_at,
    url: str | None,
    lang: str | None = None,
) -> list[DocumentIn]:
    """One document per matched IP, so mention counts and RAG filters work per IP.

    Posts that match no tracked IP are kept with ip_id=None for the Phase 2 mapper.
    """
    matches = matcher.match(text)
    if not matches:
        return [DocumentIn(source, f"{source}:{post_id}", text, published_at, None, url, lang)]
    return [
        DocumentIn(source, f"{source}:{post_id}:{ip.slug}", text, published_at, ip.id, url, lang)
        for ip in matches
    ]
