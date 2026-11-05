from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ipalpha.api.routers import chat, companies, health, ips, jobs, metrics, quality, review
from ipalpha.config import get_settings
from ipalpha.observability import setup_observability

app = FastAPI(title="IP-Alpha API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
setup_observability("api", app)
app.include_router(health.router)
app.include_router(companies.router)
app.include_router(ips.router)
app.include_router(chat.router)
app.include_router(review.router)
app.include_router(jobs.router)
app.include_router(quality.router)
app.include_router(metrics.router)
