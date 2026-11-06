"""RAG chat as a LangGraph graph: understand -> retrieve -> answer -> verify.

Nodes report progress through LangGraph's custom stream, so the API can forward status, sources,
answer tokens, and the citation check to the browser as they happen.
"""

import datetime as dt
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ipalpha.agents import tools
from ipalpha.agents.citations import _mentions, audit_citations, company_terms
from ipalpha.db.models import Company
from ipalpha.ingestion.common import utcnow
from ipalpha.ingestion.matching import IpMatcher
from ipalpha.llm import LLM, Message, StructuredOutputError
from ipalpha.llm_events import llm_context

log = logging.getLogger(__name__)

MAX_SOURCES = 8
HISTORY_TURNS = 6
_CITATION = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")
_MAX_RANGE = 20

SYSTEM_PROMPT = """You are IP-Alpha's analyst. IP-Alpha tracks fan activity for Japanese \
games, anime, and light novels and compares it with the stock prices of the companies that own \
them. A Hype Score is a z-score of fan activity against its own 28-day baseline; divergence is \
company hype minus the z-score of the stock's 5-day return over the Nikkei 225.

Answer using only the Data and Sources below. Take every number from Data. If the question \
assumes something Data contradicts (for example, it says a stock is up but Data shows it down), \
say so first. If the data doesn't explain a price move, say so plainly; it may be market-wide or \
driven by things IP-Alpha doesn't track, such as earnings or currency moves. Reply in the \
language of the question. Keep answers under 200 words. Do not recommend buying or selling."""


class ChatState(TypedDict, total=False):
    question: str
    history: list[Message]
    tickers: list[str]
    days: int
    facts: str
    sources: list[tools.DocHit]
    answer: str


@dataclass
class ChatContext:
    llm: LLM
    session: Session


class TickerPick(BaseModel):
    tickers: list[str]


def find_companies(text: str, companies: list[Company], ip_matcher: IpMatcher) -> list[str]:
    """Tickers named directly (by name, Japanese name, or ticker) or through an owned IP, where
    an IP with several owners counts for the one with the largest ownership weight."""
    found = [c.ticker for c in companies if any(_mentions(text, t) for t in company_terms(c))]
    owner: dict[int, tuple[float, str]] = {}
    for company in companies:
        for o in company.ownerships:
            if o.ip_id not in owner or o.weight > owner[o.ip_id][0]:
                owner[o.ip_id] = (o.weight, company.ticker)
    for ip in ip_matcher.match(text):
        if ip.id in owner and owner[ip.id][1] not in found:
            found.append(owner[ip.id][1])
    return found


def lookback_days(question: str) -> int:
    q = question.lower()
    if re.search(r"today|yesterday|今日|昨日", q):
        return 3
    if re.search(r"month|月", q):
        return 30
    return 7


def cited_ids(answer: str) -> list[int]:
    """Ids in [12], [3, 12], and ranges like [7-10], in first-seen order."""
    ids: list[int] = []
    for group in _CITATION.findall(answer):
        for part in group.split(","):
            lo, _, hi = re.sub(r"\s", "", part).replace("–", "-").partition("-")
            start, end = int(lo), int(hi or lo)
            span = range(start, end + 1) if 0 <= end - start < _MAX_RANGE else (start, end)
            ids += [n for n in span if n not in ids]
    return ids


def _understand(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    with llm_context("chat:understand"):
        return _understand_inner(state, runtime)


def _understand_inner(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    ctx, write = runtime.context, get_stream_writer()
    companies = tools.companies_with_ownerships(ctx.session)
    matcher = IpMatcher.from_db(ctx.session)
    question = state["question"]
    tickers = find_companies(question, companies, matcher)
    if not tickers:
        previous = [m["content"] for m in state.get("history", []) if m["role"] == "user"]
        if previous:
            tickers = find_companies(previous[-1], companies, matcher)
    if not tickers:
        tickers = _pick_with_llm(ctx.llm, question, companies)
    label = ", ".join(tickers) if tickers else "all tracked companies"
    write({"type": "status", "text": f"Looking at {label}"})
    return {"tickers": tickers, "days": lookback_days(question)}


def _pick_with_llm(llm: LLM, question: str, companies: list[Company]) -> list[str]:
    listing = "\n".join(f"- {c.ticker}: {c.name}" for c in companies)
    messages = [
        {
            "role": "system",
            "content": "Return the tickers of the companies the question is about, chosen "
            "only from the list. Return an empty list if it is about none or all of them.",
        },
        {"role": "user", "content": f"Companies:\n{listing}\n\nQuestion: {question}"},
    ]
    try:
        picked = llm.structured(messages, TickerPick).value.tickers
    except StructuredOutputError:
        return []
    valid = {c.ticker for c in companies}
    return [t for t in picked if t in valid]


def _retrieve(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    with llm_context("chat:retrieve"):
        return _retrieve_inner(state, runtime)


def _retrieve_inner(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    ctx, write = runtime.context, get_stream_writer()
    companies = {c.ticker: c for c in tools.companies_with_ownerships(ctx.session)}
    chosen = [companies[t] for t in state["tickers"]] or list(companies.values())

    blocks = []
    for company in chosen:
        block = tools.snapshot(ctx.session, company).prompt_block()
        if state["tickers"]:
            series = tools.series_block(ctx.session, company)
            if series:
                block += f"\nRecent trading days:\n{series}"
            for thesis in tools.recent_theses(ctx.session, company.ticker, limit=1):
                block += (
                    f"\nLatest analyst thesis ({thesis.date}): {thesis.body.get('headline')}. "
                    f"{thesis.body.get('claim')}"
                )
        blocks.append(block)

    ip_ids = [i for c in chosen for i in tools.company_ip_ids(c)] if state["tickers"] else None
    since = utcnow() - dt.timedelta(days=state["days"])
    sources = tools.search_documents(
        ctx.session, ctx.llm, state["question"], ip_ids=ip_ids, since=since, limit=MAX_SOURCES
    )
    if not sources:
        sources = tools.search_documents(
            ctx.session, ctx.llm, state["question"], ip_ids=ip_ids, limit=MAX_SOURCES
        )
    write({"type": "sources", "sources": [s.to_dict() for s in sources]})
    return {"facts": "\n\n".join(blocks), "sources": sources}


def build_messages(state: ChatState) -> list[Message]:
    sources = "\n".join(s.prompt_line() for s in state["sources"]) or "(no matching posts)"
    history = state.get("history", [])[-HISTORY_TURNS:]
    # Models copy a placeholder id from the instructions, so the example uses a real one.
    if state["sources"]:
        example = state["sources"][0].id
        citing = (
            f"\n\nCite the posts you rely on by their number in square brackets, one per "
            f"bracket, for example [{example}]. Only cite numbers listed under Sources."
        )
    else:
        citing = "\n\nThere are no posts to cite, so do not use square-bracket citations."
    return [
        {"role": "system", "content": SYSTEM_PROMPT + citing},
        *history,
        {
            "role": "user",
            "content": f"Data:\n{state['facts']}\n\nSources:\n{sources}\n\n"
            f"Question: {state['question']}",
        },
    ]


def _answer(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    with llm_context("chat:answer"):
        write = get_stream_writer()
        parts = []
        for token in runtime.context.llm.stream(build_messages(state)):
            parts.append(token)
            write({"type": "token", "text": token})
        return {"answer": "".join(parts)}


def _verify(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    with llm_context("chat:verify"):
        shown = {s.id for s in state["sources"]}
        cited = cited_ids(state["answer"])
        audit = audit_citations(runtime.context.session, cited, shown, state.get("tickers") or [])
        invalid = audit.unknown + audit.missing
        if invalid or audit.unrelated:
            log.warning(
                "chat answer citations unknown=%s missing=%s unrelated=%s",
                audit.unknown,
                audit.missing,
                audit.unrelated,
            )
        get_stream_writer()(
            {
                "type": "citations",
                "valid": audit.valid,
                "invalid": invalid,
                "unrelated": audit.unrelated,
            }
        )
        return {}


def build_graph():
    graph = StateGraph(ChatState, context_schema=ChatContext)
    graph.add_node("understand", _understand)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("answer", _answer)
    graph.add_node("verify", _verify)
    graph.add_edge(START, "understand")
    graph.add_edge("understand", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def run_chat(
    session: Session, llm: LLM, question: str, history: list[Message] | None = None
) -> Iterator[dict[str, Any]]:
    """Yields events: status, sources, token (many), citations, then done."""
    state: ChatState = {"question": question, "history": history or []}
    yield from build_graph().stream(
        state, context=ChatContext(llm=llm, session=session), stream_mode="custom"
    )
    yield {"type": "done"}
