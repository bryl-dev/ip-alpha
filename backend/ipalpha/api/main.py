from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from ipalpha.api.routers import chat, companies, ips, review
from ipalpha.config import get_settings
from ipalpha.db.session import get_db

app = FastAPI(title="IP-Alpha API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(companies.router)
app.include_router(ips.router)
app.include_router(chat.router)
app.include_router(review.router)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
