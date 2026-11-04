"""Hype Score: each metric is z-scored against that IP's own trailing baseline, clipped, and
combined with fixed component weights. Baselines exclude the current day (no look-ahead)."""

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Z_CLIP = 4.0


@dataclass(frozen=True)
class MetricSpec:
    component: str
    transform: Literal["level", "diff"]
    daily_agg: Literal["max", "last", "sum"]
    weekly: bool = False


METRIC_SPECS: dict[str, MetricSpec] = {
    "ccu": MetricSpec("ccu", "level", "max"),
    "review_total": MetricSpec("reviews", "diff", "last"),
    "mal_members": MetricSpec("mal", "diff", "last"),
    "mal_score": MetricSpec("mal", "diff", "last"),
    "oricon_units": MetricSpec("oricon", "level", "last", weekly=True),
    "mention_count": MetricSpec("social", "level", "sum"),
}

COMPONENT_WEIGHTS: dict[str, float] = {
    "ccu": 0.30,
    "reviews": 0.15,
    "mal": 0.20,
    "oricon": 0.15,
    "social": 0.20,
}


def rolling_z(
    series: pd.Series, window: int = 28, min_periods: int = 7, clip: float = Z_CLIP
) -> pd.Series:
    """z-score of each value against the preceding `window` observations."""
    prior = series.shift(1).rolling(window, min_periods=min_periods)
    mean, std = prior.mean(), prior.std()
    z = (series - mean) / std.replace(0, np.nan)
    # A flat baseline has std 0: no change scores 0, any change is treated as a maximal move.
    flat = (std == 0) & series.notna()
    z = z.mask(flat, np.sign(series - mean) * clip)
    return z.clip(-clip, clip)


def to_daily(points: pd.DataFrame, agg: str) -> pd.Series:
    """points has columns `date` and `value`; returns a series on a continuous daily index."""
    if points.empty:
        return pd.Series(dtype=float)
    values = points["value"].astype(float)
    daily = values.groupby(points["date"]).agg(agg).sort_index()
    daily.index = pd.DatetimeIndex(daily.index)
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    return daily.reindex(full)


def metric_z(points: pd.DataFrame, spec: MetricSpec) -> pd.Series:
    daily = to_daily(points, spec.daily_agg)
    if daily.empty:
        return daily
    if spec.weekly:
        observed = daily.dropna()
        z = rolling_z(observed, window=8, min_periods=3)
        return z.reindex(daily.index).ffill(limit=6)
    values = daily.diff() if spec.transform == "diff" else daily
    return rolling_z(values)


def ip_hype(metrics: pd.DataFrame) -> pd.DataFrame:
    """metrics: one IP's rows with columns `metric`, `date`, `value`.

    Returns a frame indexed by date with a `score` column plus one column per component.
    """
    by_component: dict[str, list[pd.Series]] = {}
    for metric, group in metrics.groupby("metric"):
        spec = METRIC_SPECS.get(str(metric))
        if spec is None:
            continue
        z = metric_z(group[["date", "value"]], spec)
        if not z.dropna().empty:
            by_component.setdefault(spec.component, []).append(z)

    if not by_component:
        return pd.DataFrame(columns=["score"])

    components = pd.DataFrame(
        {c: pd.concat(zs, axis=1).mean(axis=1) for c, zs in by_component.items()}
    )
    weights = pd.Series({c: COMPONENT_WEIGHTS[c] for c in components.columns})
    available = components.notna()
    weighted = components.fillna(0).mul(weights, axis=1).sum(axis=1)
    weight_sum = available.mul(weights, axis=1).sum(axis=1)
    components["score"] = weighted / weight_sum.replace(0, np.nan)
    return components.dropna(subset=["score"])


def company_hype(ip_scores: dict[int, pd.Series], weights: dict[int, float]) -> pd.Series:
    """Ownership-weighted mean of IP scores, over the IPs that have a score on each date."""
    if not ip_scores:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(ip_scores)
    w = pd.Series({ip_id: weights[ip_id] for ip_id in frame.columns})
    num = frame.fillna(0).mul(w, axis=1).sum(axis=1)
    den = frame.notna().mul(w, axis=1).sum(axis=1)
    return (num / den.replace(0, np.nan)).dropna()
