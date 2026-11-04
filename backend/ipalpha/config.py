from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://ipalpha:ipalpha@localhost:5433/ipalpha"
    seeds_dir: Path = REPO_ROOT / "seeds"
    cors_origins: list[str] = ["http://localhost:3000"]

    http_user_agent: str = "ip-alpha/0.1 (student research project)"
    http_timeout_s: float = 20.0

    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "ip-alpha/0.1 by u/unknown"
    reddit_subreddits: list[str] = ["anime", "Games", "LightNovels", "JRPG"]
    reddit_posts_per_subreddit: int = 200

    mal_client_id: str | None = None

    bluesky_handle: str | None = None
    bluesky_app_password: str | None = None

    # Oricon's weekly book ranking page; the light-novel ("文庫") chart URL can change, so it is
    # configurable rather than hardcoded in the scraper.
    oricon_ranking_url: str = "https://www.oricon.co.jp/rank/obb/w/"

    benchmark_ticker: str = "^N225"
    price_history_days: int = 400


@lru_cache
def get_settings() -> Settings:
    return Settings()
