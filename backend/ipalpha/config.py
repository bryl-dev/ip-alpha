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

    # Any OpenAI-compatible server works; the default is a local Ollama.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    embedding_model: str = "bge-m3"
    llm_timeout_s: float = 180.0

    normalize_batch_size: int = 100
    # bge-m3 cosine similarity between a mention and an IP term: at or above `accept`, the
    # match is taken as is; between `review` and `accept`, the LLM picks among candidates.
    mapper_accept_similarity: float = 0.85
    mapper_review_similarity: float = 0.5
    analyst_min_divergence: float = 1.0

    # Phase 3: infrastructure
    redis_url: str = "redis://localhost:6380/0"
    admin_token: str | None = None
    # Phase 3: ingestion (requests per second, burst)
    rate_limits: dict[str, tuple[float, int]] = {
        "steam": (1.0, 1),
        "steamspy": (1.0, 1),
        "jikan": (1.0, 3),
        "mal_api": (2.0, 2),
        "bluesky": (2.0, 2),
        "oricon": (0.2, 1),
    }
    http_cache_enabled: bool = True
    http_max_retries: int = 4
    # Phase 3: worker
    job_timeout_s: int = 3600
    # Phase 3: LLM resilience and cost accounting
    llm_max_retries: int = 3
    llm_price_in_per_mtok: float = 0.15
    llm_price_out_per_mtok: float = 0.60
    # Phase 3: observability
    log_json: bool = False
    log_level: str = "INFO"
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    # Phase 3: deployment
    chat_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
