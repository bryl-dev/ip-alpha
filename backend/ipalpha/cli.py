import argparse
import logging
import sys

import truststore

from ipalpha.config import get_settings
from ipalpha.db.session import session_scope
from ipalpha.jobs import DAILY_INGEST, JOBS, run_job
from ipalpha.seed import apply_seeds


def _chat(question: str) -> None:
    from ipalpha.agents.chat_graph import run_chat
    from ipalpha.llm import get_llm

    sources: list[dict] = []
    with session_scope() as session:
        for event in run_chat(session, get_llm(), question):
            if event["type"] == "status":
                print(f"[{event['text']}]", file=sys.stderr)
            elif event["type"] == "token":
                print(event["text"], end="", flush=True)
            elif event["type"] == "sources":
                sources = event["sources"]
            elif event["type"] == "citations":
                print("\n")
                for s in sources:
                    if s["id"] in event["valid"]:
                        print(f"[{s['id']}] {s['source']} {s['url'] or ''}")
                if event["invalid"]:
                    print(f"(dropped citations to unknown ids: {event['invalid']})")


def main() -> None:
    # Use the OS certificate store so TLS works behind proxies that install their own root CA.
    truststore.inject_into_ssl()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="ipalpha")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="load seeds/*.yaml into the database")
    ingest = sub.add_parser("ingest", help="run one ingestion source, or all of them")
    ingest.add_argument("source", choices=[*JOBS.keys() - {"score"}, "all"])
    sub.add_parser("score", help="recompute Hype Scores and divergence")
    sub.add_parser("enrich", help="normalize, map, and embed new documents with the LLM")
    analyze = sub.add_parser("analyze", help="write analyst theses for divergent companies")
    analyze.add_argument("--ticker", action="append", help="limit to a ticker (repeatable)")
    analyze.add_argument(
        "--force", action="store_true", help="write even below the divergence threshold"
    )
    pipeline = sub.add_parser("pipeline", help="run the agent pipeline graph")
    pipeline.add_argument(
        "--stages", help="comma-separated subset of fetch,normalize,map_ip,embed,score,analyze"
    )
    chat = sub.add_parser("chat", help="ask the analyst a question")
    chat.add_argument("question")
    sub.add_parser("worker", help="run the scheduler")
    args = parser.parse_args()

    if args.command == "seed":
        with session_scope() as session:
            companies, ips = apply_seeds(session, get_settings().seeds_dir)
        print(f"seeded {companies} companies and {ips} IPs")
    elif args.command == "ingest":
        for name in DAILY_INGEST if args.source == "all" else [args.source]:
            run_job(name)
    elif args.command in ("score", "enrich"):
        run_job(args.command)
    elif args.command in ("analyze", "pipeline"):
        from ipalpha.agents.pipeline_graph import STAGES, run_pipeline

        if args.command == "analyze":
            state = run_pipeline(["analyze"], tickers=args.ticker, force_analyze=args.force)
        else:
            stages = args.stages.split(",") if args.stages else STAGES
            state = run_pipeline(stages)
        for name, result in state["results"].items():
            print(f"{name}: {result}")
        for error in state["errors"]:
            print(f"error: {error}", file=sys.stderr)
    elif args.command == "chat":
        _chat(args.question)
    elif args.command == "worker":
        from ipalpha.worker import run_scheduler

        run_scheduler()


if __name__ == "__main__":
    main()
