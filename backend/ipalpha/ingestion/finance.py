import datetime as dt
import logging

import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ipalpha.config import get_settings
from ipalpha.db.models import Company, StockPrice
from ipalpha.ingestion.common import ca_bundle_path

log = logging.getLogger(__name__)


def history_to_rows(ticker: str, history: pd.DataFrame) -> list[dict]:
    rows = []
    for ts, row in history.iterrows():
        if pd.isna(row["Close"]):
            continue
        rows.append(
            {
                "ticker": ticker,
                "date": pd.Timestamp(ts).date(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
        )
    return rows


def run(session: Session, days: int | None = None) -> int:
    settings = get_settings()
    tickers = [c.ticker for c in session.scalars(select(Company))]
    tickers.append(settings.benchmark_ticker)
    start = dt.date.today() - dt.timedelta(days=days or settings.price_history_days)

    http = curl_requests.Session(impersonate="chrome", verify=ca_bundle_path())
    total = 0
    for ticker in tickers:
        try:
            # auto_adjust so splits (e.g. Nintendo's 2022 10:1) don't look like crashes.
            history = yf.Ticker(ticker, session=http).history(
                start=start.isoformat(), auto_adjust=True
            )
        except Exception as exc:
            log.warning("yfinance failed for %s: %s", ticker, exc)
            continue
        rows = history_to_rows(ticker, history)
        if not rows:
            log.warning("yfinance returned no rows for %s", ticker)
            continue
        stmt = insert(StockPrice).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume")},
        )
        session.execute(stmt)
        total += len(rows)
    return total
