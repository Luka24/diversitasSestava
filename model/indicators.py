"""Technical indicators — Pine-compatible (Wilder RMA where Pine uses ta.rma).

All functions take and return pandas.Series unless noted.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """Standard EMA matching Pine's ta.ema (adjust=False, span=length)."""
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (Pine's ta.rma): alpha = 1/length, adjust=False."""
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def stdev_pop(series: pd.Series, length: int) -> pd.Series:
    """Population stdev — Pine's ta.stdev uses ddof=0."""
    return series.rolling(length, min_periods=length).std(ddof=0)


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder RSI matching Pine ta.rsi."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 (no losses), RSI = 100 (Pine behaviour)
    rsi_val = rsi_val.where(avg_loss != 0, 100.0)
    return rsi_val


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """ADX matching Pine implementation in the strategy:
        up   = change(high);  down = -change(low)
        pDM  = up   if up>down AND up>0   else 0
        nDM  = down if down>up AND down>0 else 0
        trur = rma(tr, len)
        pDI  = 100 * rma(pDM, len) / trur
        nDI  = 100 * rma(nDM, len) / trur
        dx   = 100 * |pDI-nDI| / (pDI+nDI)
        adx  = rma(dx, len)
    """
    up = high.diff()
    down = -low.diff()
    p_dm = np.where((up > down) & (up > 0), up, 0.0)
    n_dm = np.where((down > up) & (down > 0), down, 0.0)
    p_dm = pd.Series(p_dm, index=high.index)
    n_dm = pd.Series(n_dm, index=high.index)

    tr = true_range(high, low, close)
    trur = rma(tr, length)
    p_di = 100.0 * rma(p_dm, length) / trur.replace(0, np.nan)
    n_di = 100.0 * rma(n_dm, length) / trur.replace(0, np.nan)
    s = p_di + n_di
    dx = (100.0 * (p_di - n_di).abs() / s.replace(0, np.nan)).fillna(0.0)
    return rma(dx, length)


def highest(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).max()


def lowest(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).min()


def bars_since(condition: pd.Series) -> pd.Series:
    """Pine's ta.barssince: number of bars since condition was last True.
    Returns NaN before any True occurs. 0 on the bar the condition is True.
    """
    cond = condition.fillna(False).astype(bool).to_numpy()
    n = len(cond)
    out = np.full(n, np.nan)
    counter = -1  # not seen yet
    for i in range(n):
        if cond[i]:
            counter = 0
        elif counter >= 0:
            counter += 1
        if counter >= 0:
            out[i] = counter
    return pd.Series(out, index=condition.index)


def cummax_from_close(close: pd.Series) -> pd.Series:
    """Running peak from the first valid close — mirrors the var float peakPrice
    pattern in Pine where peakPrice := max(peakPrice, close)."""
    return close.cummax()


# ── added for the ETF sleeve ──────────────────────────────────────────────────
# Everything above is Pine-compatible because it had to match a TradingView
# script. Nothing below has a Pine counterpart; these are the pieces the ETF
# strategies need and the crypto ports never did.


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        length: int = 14) -> pd.Series:
    """Average True Range, Wilder smoothing (matches Pine's ta.atr).

    The reason the ETF sleeve needs this and the crypto sleeve did not: a fixed
    percentage stop is a different animal on an instrument whose annualised
    volatility is 12 % than on one whose volatility is 60 %. A 12 % trailing
    stop is a routine week in BTC and a once-a-decade event in a global
    aggregate bond fund. ATR expresses the stop in units of the instrument's own
    recent range, so one parameter can cover both.
    """
    return rma(true_range(high, low, close), length)


def atr_pct(high: pd.Series, low: pd.Series, close: pd.Series,
            length: int = 14) -> pd.Series:
    """ATR as a percentage of price — the scale-free form used for sizing."""
    return atr(high, low, close, length) / close * 100.0


def realized_vol(close: pd.Series, length: int = 60,
                 trading_days: int = 252) -> pd.Series:
    """Annualised realised volatility of log returns, in percent.

    `trading_days` is a parameter and not 365 because that constant is the
    single most common way a crypto codebase reports a wrong number on equities:
    it inflates every annualised figure by sqrt(365/252) = 1.20.
    """
    lr = np.log(close / close.shift(1))
    return stdev_pop(lr, length) * np.sqrt(trading_days) * 100.0


def bbands(close: pd.Series, length: int = 20, mult: float = 2.0
           ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(lower, mid, upper) Bollinger Bands on the simple moving average."""
    mid = sma(close, length)
    sd = stdev_pop(close, length)
    return mid - mult * sd, mid, mid + mult * sd


def bb_pctb(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.Series:
    """%B: 0 at the lower band, 1 at the upper. NaN when the band has no width."""
    lo, _, up = bbands(close, length, mult)
    width = (up - lo).replace(0, np.nan)
    return (close - lo) / width


def adaptive_bb_mult(close: pd.Series, length: int = 20, vol_len: int = 100,
                     lo: float = 1.5, hi: float = 3.0) -> pd.Series:
    """Band width that widens when volatility is high relative to its own past.

    A fixed 2-sigma band is not a fixed event rate: in a calm tape it is touched
    constantly and in a stressed one almost never, so a mean-reversion entry
    built on it fires at the wrong times in both regimes. Scaling the multiplier
    by the volatility percentile keeps the touch rate roughly stationary.
    """
    sd = stdev_pop(close.pct_change(), length)
    pct = sd.rolling(vol_len, min_periods=vol_len // 2).rank(pct=True)
    return (lo + (hi - lo) * pct).clip(lo, hi)


def donchian_mid(high: pd.Series, low: pd.Series, length: int) -> pd.Series:
    """The `trackline` of the crypto ports, named for what it is."""
    return (highest(high, length) + lowest(low, length)) / 2.0


def zscore(series: pd.Series, length: int) -> pd.Series:
    sd = stdev_pop(series, length).replace(0, np.nan)
    return (series - sma(series, length)) / sd


def total_return(close: pd.Series, length: int) -> pd.Series:
    """Simple `length`-bar return — the momentum input for cross-sectional
    ranking. Kept separate from `zscore` because rotation ranks on raw return
    and sizes on volatility, and conflating the two hides which is doing what."""
    return close / close.shift(length) - 1.0
