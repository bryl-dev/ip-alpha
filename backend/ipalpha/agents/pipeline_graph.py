"""The scheduled agent pipeline as a LangGraph graph:

    fetch -> normalize -> map_ip -> embed -> score -> analyze

Every stage reads its input from the database and commits its own output, so any subset of
stages can run on its own (the worker runs ingestion, enrichment, and analysis at different
times of day). A failing stage is logged and recorded in `errors`, and the graph moves on.
"""

import logging
import operator
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy.orm import Session

from ipalpha.agents import analyst, embed, mapper, normalize
from ipalpha.config import Settings, get_settings
from ipalpha.db.session import session_scope
from ipalpha.llm import LLM, LLM_UNAVAILABLE, get_llm
from ipalpha.scoring import job as scoring_job

log = logging.getLogger(__name__)

STAGES = ("fetch", "normalize", "map_ip", "embed", "score", "analyze")
ENRICH_STAGES = ("normalize", "map_ip", "embed")


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {**a, **b}


class PipelineState(TypedDict):
    stages: list[str]
    results: Annotated[dict[str, Any], _merge]
    errors: Annotated[list[str], operator.add]


@dataclass
class PipelineContext:
    llm: LLM
    settings: Settings
    session_factory: Callable[[], AbstractContextManager[Session]] = session_scope
    sources: Sequence[str] | None = None
    tickers: list[str] | None = None
    force_analyze: bool = False


def _fetch(ctx: PipelineContext) -> dict[str, Any]:
    from ipalpha.jobs import DAILY_INGEST, run_job

    return {name: run_job(name) for name in ctx.sources or DAILY_INGEST}


def _in_session(fn: Callable[[Session, PipelineContext], Any]) -> Callable[[PipelineContext], Any]:
    def work(ctx: PipelineContext) -> Any:
        with ctx.session_factory() as session:
            return fn(session, ctx)

    return work


WORK: dict[str, Callable[[PipelineContext], Any]] = {
    "fetch": _fetch,
    "normalize": _in_session(lambda s, c: normalize.run(s, c.llm, c.settings)),
    "map_ip": _in_session(lambda s, c: mapper.run(s, c.llm, c.settings)),
    "embed": _in_session(lambda s, c: embed.run(s, c.llm, c.settings)),
    "score": _in_session(lambda s, c: scoring_job.run(s)),
    "analyze": _in_session(
        lambda s, c: analyst.run(s, c.llm, c.settings, tickers=c.tickers, force=c.force_analyze)
    ),
}


def _node(name: str) -> Callable[[PipelineState, Runtime[PipelineContext]], dict[str, Any]]:
    def node(state: PipelineState, runtime: Runtime[PipelineContext]) -> dict[str, Any]:
        if name not in state["stages"]:
            return {}
        ctx = runtime.context
        try:
            result = WORK[name](ctx)
        except LLM_UNAVAILABLE as exc:
            msg = f"{name}: language model unreachable at {ctx.llm.base_url} ({exc})"
            log.error(msg)
            return {"errors": [msg]}
        except Exception as exc:
            log.exception("pipeline stage %s failed", name)
            return {"errors": [f"{name}: {exc}"]}
        log.info("pipeline stage %s: %s", name, result)
        return {"results": {name: result}}

    return node


def build_graph():
    graph = StateGraph(PipelineState, context_schema=PipelineContext)
    previous = START
    for name in STAGES:
        graph.add_node(name, _node(name))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


def run_pipeline(
    stages: Sequence[str] = STAGES,
    *,
    llm: LLM | None = None,
    settings: Settings | None = None,
    session_factory: Callable[[], AbstractContextManager[Session]] | None = None,
    sources: Sequence[str] | None = None,
    tickers: list[str] | None = None,
    force_analyze: bool = False,
) -> PipelineState:
    unknown = set(stages) - set(STAGES)
    if unknown:
        raise ValueError(f"unknown pipeline stages: {sorted(unknown)}")
    context = PipelineContext(
        llm=llm or get_llm(),
        settings=settings or get_settings(),
        session_factory=session_factory or session_scope,
        sources=sources,
        tickers=tickers,
        force_analyze=force_analyze,
    )
    state: PipelineState = {"stages": list(stages), "results": {}, "errors": []}
    return build_graph().invoke(state, context=context)
