import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ipalpha.scoring.divergence import divergence, excess_return
from ipalpha.scoring.hype import Z_CLIP, company_hype, ip_hype, rolling_z


def _days(n: int, start: str = "2026-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="D")


def test_rolling_z_excludes_current_day_and_clips():
    rng = np.random.default_rng(0)
    values = pd.Series(100 + rng.normal(0, 1, 40), index=_days(40))
    values.iloc[-1] = 1_000  # a huge spike
    z = rolling_z(values)
    assert z.iloc[:7].isna().all()  # not enough history yet
    assert z.iloc[-1] == Z_CLIP
    # The spike must not leak into its own baseline.
    assert abs(z.iloc[-2]) < Z_CLIP


def test_rolling_z_flat_baseline():
    values = pd.Series([0.0] * 20 + [0.0, 5.0], index=_days(22))
    z = rolling_z(values)
    assert z.iloc[-2] == 0
    assert z.iloc[-1] == Z_CLIP


def _metric_rows(metric: str, values: list[float]) -> pd.DataFrame:
    dates = [d.date() for d in _days(len(values))]
    return pd.DataFrame({"metric": metric, "date": dates, "value": values})


def test_ip_hype_renormalizes_over_available_components():
    ccu = [1000.0] * 29 + [1000.0, 5000.0]
    hype = ip_hype(_metric_rows("ccu", ccu))
    assert set(hype.columns) == {"ccu", "score"}
    # With only one component, the score equals that component's z.
    assert hype["score"].iloc[-1] == pytest.approx(hype["ccu"].iloc[-1])
    assert hype["score"].iloc[-1] == Z_CLIP


def test_ip_hype_diff_metric_uses_daily_change():
    # Members grow 100/day, then jump by 1000: the change, not the level, should spike.
    members = [10_000.0 + 100 * i for i in range(30)]
    members.append(members[-1] + 1000)
    hype = ip_hype(_metric_rows("mal_members", members))
    assert hype["mal"].iloc[-1] == Z_CLIP
    assert hype["mal"].iloc[-2] == 0


def test_ip_hype_accepts_object_dtype_values():
    rows = _metric_rows("ccu", [1000.0] * 29 + [5000.0])
    rows["value"] = rows["value"].astype(object)
    assert ip_hype(rows)["score"].iloc[-1] == Z_CLIP


def test_ip_hype_ignores_unknown_metrics():
    assert ip_hype(_metric_rows("review_positive_ratio", [0.9] * 30)).empty


def test_company_hype_weights_and_missing_values():
    idx = _days(3)
    scores = {1: pd.Series([1.0, 2.0, np.nan], idx), 2: pd.Series([3.0, np.nan, np.nan], idx)}
    result = company_hype(scores, {1: 0.25, 2: 0.75})
    assert result.iloc[0] == pytest.approx(0.25 * 1 + 0.75 * 3)
    assert result.iloc[1] == pytest.approx(2.0)
    assert len(result) == 2  # the all-missing day is dropped


def test_excess_return_and_divergence_sign():
    idx = pd.bdate_range("2026-01-01", periods=90)
    bench = pd.Series(np.linspace(100, 110, 90), idx)
    stock = bench.copy()
    assert excess_return(stock, bench).abs().max() == pytest.approx(0, abs=1e-12)

    # Hype jumps on the last day while the stock is flat relative to the market.
    hype = pd.Series(0.0, idx)
    hype.iloc[-1] = 3.0
    rng = np.random.default_rng(1)
    noisy = stock * (1 + rng.normal(0, 0.002, 90))
    div = divergence(hype, noisy, bench)
    assert div.index[-1] == idx[-1]
    assert div["divergence"].iloc[-1] > 2
    assert dt.date(2026, 1, 1) not in div.index  # needs return history first
