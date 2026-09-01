"""The cost model — one definition, imported by the dashboards and the harness.

Two mistakes this exists to prevent, both of which produced real disagreements
between the live dashboard and written reports:

1. **Charging on `signal_changed`.** The position is *yesterday's* signal, so the
   trade happens one bar after the flip. Charging on the flip prices it a day
   early: same number of charges, different daily series, different Sortino.

2. **Charging for the position you started with.** On a window that begins while
   the strategy is already long (which happens whenever the warm-up boundary
   falls inside a trade), `pos.diff()` is NaN on the first bar. Filling that with
   `abs(pos)` invents an entry that never happened.

Turnover is therefore `pos.diff().abs()` with the first bar set to zero.
"""
from __future__ import annotations

import pandas as pd


def turnover(pos: pd.Series) -> pd.Series:
    """Fraction of capital traded on each bar. Zero on the first bar: whatever the
    position is there was inherited from before the window, not opened in it."""
    return pos.diff().abs().fillna(0.0)


def net_returns(pos: pd.Series, ret: pd.Series, fee_per_side_pct: float = 0.0,
                traded: "pd.Series | None" = None) -> pd.Series:
    """Position-scaled returns minus trading cost.

    `fee_per_side_pct` covers fee + slippage for one side; a round trip pays it
    twice because turnover registers on both the entry and the exit bar.

    `traded` says how much was actually bought or sold. It defaults to the change
    in `pos`, which is right for a position that only moves when you trade it.
    It is NOT right once a floor is in play, because the leftover holding drifts
    with the price and that is not a transaction. Pass `traded_fraction(df, cfg)`
    in that case.
    """
    sr = pos * ret.reindex(pos.index).fillna(0.0)
    if fee_per_side_pct:
        t = turnover(pos) if traded is None else traded.reindex(pos.index).fillna(0.0)
        sr = sr - t * (fee_per_side_pct / 100.0)
    return sr
