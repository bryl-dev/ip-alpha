from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ipalpha.api.schemas import IpCandidateOut
from ipalpha.db.models import IpCandidate
from ipalpha.db.session import get_db

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/ip-candidates", response_model=list[IpCandidateOut])
def ip_candidates(
    status: str = "pending",
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[IpCandidate]:
    """Titles the mapper couldn't tie to a tracked IP, most-mentioned first."""
    return list(
        db.scalars(
            select(IpCandidate)
            .where(IpCandidate.status == status)
            .order_by(IpCandidate.mention_count.desc(), IpCandidate.last_seen.desc())
            .limit(limit)
        )
    )
