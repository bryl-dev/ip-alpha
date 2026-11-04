import pandas as pd

from ipalpha.scoring.hype import rolling_z

RETURN_HORIZON_DAYS = 5


def excess_return(
    close: pd.Series, benchmark: pd.Series, horizon: int = RETURN_HORIZON_DAYS
) -> pd.Series:
    """Trailing `horizon`-trading-day return of the stock minus the benchmark's."""
    aligned = pd.concat({"stock": close, "bench": benchmark}, axis=1, sort=True).dropna()
    returns = aligned.pct_change(horizon, fill_method=None)
    return (returns["stock"] - returns["bench"]).dropna()


def divergence(company_hype: pd.Series, close: pd.Series, benchmark: pd.Series) -> pd.DataFrame:
    """High divergence = high hype while the stock lags the market.

    Evaluated on trading days only; hype is taken as of the same JST calendar day.
    """
    ret_z = rolling_z(excess_return(close, benchmark), window=60, min_periods=20)
    frame = pd.concat({"hype": company_hype, "return_z": ret_z}, axis=1, sort=True).dropna()
    frame["divergence"] = frame["hype"] - frame["return_z"]
    return frame
