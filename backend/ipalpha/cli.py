import argparse
import logging

import truststore

from ipalpha.config import get_settings
from ipalpha.db.session import session_scope
from ipalpha.jobs import DAILY_INGEST, JOBS, run_job
from ipalpha.seed import apply_seeds


def main() -> None:
    # Use the OS certificate store so TLS works behind proxies that install their own root CA.
    truststore.inject_into_ssl()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="ipalpha")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="load seeds/*.yaml into the database")
    ingest = sub.add_parser("ingest", help="run one ingestion source, or all of them")
    ingest.add_argument("source", choices=[*JOBS.keys() - {"score"}, "all"])
    sub.add_parser("score", help="recompute Hype Scores and divergence")
    sub.add_parser("worker", help="run the scheduler")
    args = parser.parse_args()

    if args.command == "seed":
        with session_scope() as session:
            companies, ips = apply_seeds(session, get_settings().seeds_dir)
        print(f"seeded {companies} companies and {ips} IPs")
    elif args.command == "ingest":
        for name in DAILY_INGEST if args.source == "all" else [args.source]:
            run_job(name)
    elif args.command == "score":
        run_job("score")
    elif args.command == "worker":
        from ipalpha.worker import run_scheduler

        run_scheduler()


if __name__ == "__main__":
    main()
