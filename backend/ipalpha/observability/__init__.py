from fastapi import FastAPI

from ipalpha.config import get_settings
from ipalpha.llm_events import register_sink

from .llm_sink import DbLlmSink, LangfuseSink
from .logging import configure_logging
from .middleware import RequestContextMiddleware


def register_llm_event_sinks(service: str) -> None:
    """Registered from setup_observability and from test conftest after clear_sinks."""
    register_sink(DbLlmSink(service))
    register_sink(LangfuseSink())


def setup_observability(service: str, app: FastAPI | None = None) -> None:
    settings = get_settings()
    configure_logging(json=settings.log_json, level=settings.log_level)
    register_llm_event_sinks(service)
    if app is not None:
        app.add_middleware(RequestContextMiddleware)
