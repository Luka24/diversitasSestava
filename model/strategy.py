"""Diversitas Lean strategy engine — Python port of `diversitas_lean.pine`.

Lean is the stripped-down variant of Diversitas Pro v3:
  - Kijun trackline (direction)
  - 200 MA (regime MA — hard block when below + falling)
  - Blow-off exit
  - Range filter via trackline slope over N bars
  - One state machine (BULL / BEAR) — no conviction score, no separate display

The 50 MA entry gate, the entry-distance gate and the vol-shock exit were removed
on 2026-08-03 after each was shown to leave the position series bit-identical.
The 50 MA and the annualised vol are still COMPUTED — the dashboard plots both —
they simply no longer decide anything. See `config.py` for the evidence and for
the four parameter values at which vol_shock happened to be inert.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import math

import numpy as np
import pandas as pd

from model import indicators as ind
from .config import LeanConfig, DEFAULT_CONFIG


# Display-only lengths. These used to be config fields (`ma_med_len`,
# `vol_lookback`). They are constants now because nothing reads them on the
# signal path any more — making them tunable would invite someone to sweep a
# knob that cannot change a single trade. Both stay well under `ma_long_len`, so
# `required_history` is unaffected (still 220).
MA_MED_LEN = 50
VOL_LOOKBACK = 20

# State codes
S_BULL = 1
S_NEUTRAL = 2  # only used for displayState (HEDGED background)
S_BEAR = 3


@dataclass
class StrategyResult:
    df: pd.DataFrame
    summary: dict


def compute_features(daily: pd.DataFrame, btc_daily: Optional[pd.DataFrame],
                     cfg: LeanConfig) -> pd.DataFrame:
    """Compute every per-bar feature used by the state machine."""
    df = daily.copy()
    high, low, close = df["high"], df["low"], df["close"]

    # --- Trackline (Kijun) ---
    track_high = ind.highest(high, cfg.track_period)
    track_low = ind.lowest(low, cfg.track_period)
    df["trackline"] = (track_high + track_low) / 2.0
    df["track_rising"] = df["trackline"] > df["trackline"].shift(1)
    # NOTE the shape of this: ONE comparison of today against the bar
    # `track_slope_bars` ago. It does NOT require the trackline to have risen on
    # each of those bars — it may fall for nine days and still pass, as long as
    # today sits above where it was ten days back. The every-bar version is
    # `track_rising` above, which only colours the chart.
    df["track_ref"] = df["trackline"].shift(cfg.track_slope_bars)
    df["track_rising_window"] = df["trackline"] > df["track_ref"]
    buf_amt = df["trackline"] * (cfg.track_buf_pct / 100.0)
    df["tl_upper"] = df["trackline"] + buf_amt
    df["tl_lower"] = df["trackline"] - buf_amt
    df["above_tl"] = close > df["tl_upper"]
    df["below_tl"] = close < df["tl_lower"]
    df["dist_pct"] = (close - df["trackline"]) / df["trackline"] * 100.0

    # --- Moving averages ---
    # ma_med / above_ma_med are display-only: drawn on the chart, not part of
    # bull_condition.
    ma_med = ind.sma(close, MA_MED_LEN)
    ma_long = ind.sma(close, cfg.ma_long_len)
    df["ma_med"] = ma_med
    df["ma_long"] = ma_long
    df["above_ma_med"] = close > ma_med
    df["above_ma_long"] = close > ma_long
    df["ma_long_ref"] = ma_long.shift(cfg.ma_slope)
    df["ma_long_rising"] = ma_long > df["ma_long_ref"]
    df["ma_long_falling"] = ma_long < df["ma_long_ref"]
    df["bear_regime"] = (~df["above_ma_long"]) & df["ma_long_falling"]
    df["regime_ok"] = ~df["bear_regime"]

    # --- RSI (only used by blow-off detector) ---
    df["rsi"] = ind.rsi(close, cfg.rsi_len)

    # --- Volatility (display + position sizing; no longer gates anything) ---
    log_ret = np.log(close / close.shift(1))
    df["log_ret"] = log_ret
    daily_std = ind.stdev_pop(log_ret, VOL_LOOKBACK)
    df["annual_vol"] = daily_std * math.sqrt(cfg.trading_days) * 100.0
    vol_avg50 = ind.sma(df["annual_vol"], 50)
    df["vol_avg50"] = vol_avg50

    # --- BTC filter (optional) ---
    if cfg.use_btc_filter and btc_daily is not None and not btc_daily.empty:
        btc_close = btc_daily["close"]
        btc_ema50 = ind.ema(btc_close, 50)
        btc_bull = (btc_close > btc_ema50).reindex(df.index).ffill().fillna(False)
        df["btc_bull"] = btc_bull
        df["btc_filter_ok"] = btc_bull
    else:
        df["btc_bull"] = True
        df["btc_filter_ok"] = True

    # --- Entry / exit conditions ---
    # Donchian breakout confirmation (optional; default OFF → factor is all-True).
    if getattr(cfg, "use_donchian", False):
        dc_hi = ind.highest(high, cfg.donchian_period)
        dc_lo = ind.lowest(low, cfg.donchian_period)
        pos_in_chan = (close - dc_lo) / (dc_hi - dc_lo).replace(0, np.nan)
        df["donchian_ok"] = (pos_in_chan > cfg.donchian_top_frac).fillna(False)
        # Display only. `donchian_trigger` is the close the gate would need today:
        # it is the same inequality solved for price, so the dashboard never
        # restates the formula and cannot drift away from it.
        df["dc_hi"], df["dc_lo"] = dc_hi, dc_lo
        df["donchian_pos"] = pos_in_chan
        df["donchian_trigger"] = dc_lo + cfg.donchian_top_frac * (dc_hi - dc_lo)
    else:
        df["donchian_ok"] = True
        df["dc_hi"] = df["dc_lo"] = np.nan
        df["donchian_pos"] = df["donchian_trigger"] = np.nan

    df["trend_break"] = df["below_tl"]
    df["blowoff"] = (df["dist_pct"] > cfg.blowoff_dist_pct) & (df["rsi"] > 80)

    # `above_ma_med` and `dist_entry_ok` used to sit in here; both were implied by
    # the terms that remain, so dropping them changed nothing.
    #
    # `~blowoff` is the opposite case and is here on purpose. Blow-off is an EXIT
    # rule, and the state machine only consults exits while already long — so
    # without this term the same bar can be a valid buy and a screaming sell at
    # once. That is not a corner case: blow-off needs dist_pct > 25 % while entry
    # needs only > 3 %, so EVERY blow-off bar clears the entry hurdle. 30 of the 31
    # blow-off bars in the history satisfy every other entry condition too.
    #
    # It changes nothing today: reentry_hold blocks re-entry for 15 bars after the
    # blow-off exit, and blow-off runs are shorter than that, so the frozen
    # reference is reproduced bit for bit. It is not inert by construction, only by
    # coincidence of a parameter that later steps are about to touch — with the
    # pause removed, 12 of 32 entries land on a blow-off bar. Guarded by
    # `test_no_entry_while_an_exit_rule_is_firing`, which removes the pause so the
    # test can actually fail.
    # ENTRY GATE CHANGED 2026-08-10. `above_tl` used to sit here; the gate is now
    # `donchian_ok`. The trackline has NOT gone away — it still drives the exit
    # (`below_tl`), the slope condition and the blow-off distance — so `above_tl`
    # stays computed for the display state and the dashboard. It simply no longer
    # decides entry. See config.py for the evidence and for the limits on it.
    df["bull_condition"] = (
        df["donchian_ok"]
        & df["track_rising_window"]
        & df["regime_ok"]
        & df["btc_filter_ok"]
        & ~df["blowoff"]
    ).fillna(False)

    # --- Convenience for dashboard ---
    df["green_dot"] = df["bull_condition"]
    df["red_dot"] = df["below_tl"]
    return df


def run_state_machine(df: pd.DataFrame, cfg: LeanConfig) -> pd.DataFrame:
    """Forward pass — replicates the Lean state machine.

    Differences from the Full state machine:
      - Only one signal state (BULL/BEAR), no separate raw/display logic
      - bars_since_signal resets on BOTH BULL and BEAR transitions
      - bullHoldCount resets to 0 (not 1)
      - No weekend filter
      - displayState evaluated each bar from instantaneous conditions
    """
    n = len(df)
    signal_state = np.full(n, S_BEAR, dtype=np.int8)
    display_state = np.full(n, S_BEAR, dtype=np.int8)
    bars_since_signal = np.zeros(n, dtype=np.int32)
    below_count = np.zeros(n, dtype=np.int32)
    bull_hold = np.zeros(n, dtype=np.int32)
    signal_changed = np.zeros(n, dtype=bool)
    target_alloc = np.zeros(n, dtype=np.float32)

    cur_sig = S_BEAR
    cur_disp = S_BEAR
    prev_sig = S_BEAR
    bars_since_sig = 999
    below_c = 0
    bull_hold_c = 0

    below_arr = df["below_tl"].fillna(False).to_numpy()
    above_arr = df["above_tl"].fillna(False).to_numpy()
    bull_arr = df["bull_condition"].fillna(False).to_numpy()
    blowoff_arr = df["blowoff"].fillna(False).to_numpy()
    annual_vol_arr = df["annual_vol"].fillna(0.0).to_numpy()

    for i in range(n):
        bars_since_sig += 1

        # --- Counters (run every bar) ---
        if below_arr[i]:
            below_c += 1
        else:
            below_c = 0
        if bull_arr[i]:
            bull_hold_c += 1
        else:
            bull_hold_c = 0

        # --- Transitions ---
        if cur_sig == S_BULL:
            # BEAR exits — instant, but trend_break needs grace bars
            if below_arr[i] and below_c >= cfg.exit_grace_bars:
                cur_sig = S_BEAR
                bars_since_sig = 0
            elif blowoff_arr[i]:
                cur_sig = S_BEAR
                bars_since_sig = 0
        elif cur_sig == S_BEAR:
            if (bull_arr[i]
                    and bull_hold_c >= cfg.confirm_bars
                    and bars_since_sig >= cfg.reentry_hold):
                cur_sig = S_BULL
                bars_since_sig = 0

        # --- Display state (no confirm bars on BULL/NEUTRAL transitions) ---
        if below_arr[i] and below_c >= cfg.exit_grace_bars:
            cur_disp = S_BEAR
        elif above_arr[i] and bull_arr[i]:
            cur_disp = S_BULL
        elif above_arr[i] and not bull_arr[i]:
            cur_disp = S_NEUTRAL
        # else: hold

        # --- Allocation (BINARY 0 / 100) ---
        # Deliberate deviation from Pine: Pine's targetAlloc is the
        # vol-scaled `100 * volScale` value (e.g. 50 % when vol = 2× target).
        # We treat the signal as the source of truth — fully in or fully
        # out. `use_vol_sizing` and `target_vol_pct` are kept in Config for
        # future use (e.g. position-sizing layers) but no longer modulate
        # the dashboard's reported allocation.
        target_alloc[i] = 100.0 if cur_sig == S_BULL else 0.0

        signal_changed[i] = (cur_sig != prev_sig)
        prev_sig = cur_sig

        signal_state[i] = cur_sig
        display_state[i] = cur_disp
        bars_since_signal[i] = bars_since_sig
        below_count[i] = below_c
        bull_hold[i] = bull_hold_c

    df = df.copy()
    df["signal_state"] = signal_state
    df["display_state"] = display_state
    df["bars_since_signal"] = bars_since_signal
    df["below_count"] = below_count
    df["bull_hold"] = bull_hold
    df["signal_changed"] = signal_changed
    df["target_alloc"] = target_alloc
    return df


def _state_label(code: int, display: bool = False) -> str:
    if code == S_BULL:
        return "BULL"
    if code == S_NEUTRAL:
        return "HEDGED" if display else "NEUTRAL"
    return "BEAR"


def build_summary(df: pd.DataFrame) -> dict:
    """Latest-bar status — mirrors Pine table layout."""
    last = df.iloc[-1]
    bear = bool(last["bear_regime"])
    above_long = bool(last["above_ma_long"])
    if bear:
        ma_status = "BEAR (blocked)"
    elif above_long:
        ma_status = "ABOVE"
    else:
        ma_status = "BELOW"
    return {
        "time": last.name,
        "close": float(last["close"]),
        "signal": _state_label(int(last["signal_state"])),
        "regime": _state_label(int(last["display_state"]), display=True),
        "ma_long_status": ma_status,
        "bear_regime": bear,
        "above_ma_med": bool(last["above_ma_med"]),
        "trackline": float(last["trackline"]),
        "track_rising_window": bool(last["track_rising_window"]),
        "dist_pct": float(last["dist_pct"]),
        "annual_vol": float(last["annual_vol"]),
        "target_alloc": float(last["target_alloc"]),
        "blowoff": bool(last["blowoff"]),
        "btc_bull": bool(last["btc_bull"]),
        "rsi": float(last["rsi"]) if pd.notna(last["rsi"]) else float("nan"),
    }


def run_strategy(daily: pd.DataFrame,
                 btc_daily: Optional[pd.DataFrame] = None,
                 config: LeanConfig = DEFAULT_CONFIG) -> StrategyResult:
    df = compute_features(daily, btc_daily, config)
    df = run_state_machine(df, config)
    return StrategyResult(df=df, summary=build_summary(df))


def _sleeve_path(df: pd.DataFrame, config: LeanConfig):
    """Walk the two sleeves bar by bar and return (held, traded).

    The holding is NOT rebalanced. An exit sells down to the floor once and the
    remainder is then left alone, so its share of the portfolio moves with the
    price: it grows when the asset rises and shrinks when it falls. On BTC it has
    ranged from 1.6 % to 6.9 % while flat.

    `traded` is therefore not the change in `held`. Drift changes the share
    without a trade, and charging for it would price a transaction that never
    happens. Only the two rebalances at a signal switch cost anything.
    """
    # `prev_signal_state` is materialised by trim_warmup, so a caller handing in
    # an untrimmed frame does not have it. Shift here rather than raise: the
    # dashboard passes the untrimmed frame so that the first bar of the window
    # has a real return, and it should not have to care which columns that frame
    # happens to carry.
    if "prev_signal_state" in df.columns:
        prev = df["prev_signal_state"]
    else:
        prev = df["signal_state"].shift(1)
    bull = (prev == S_BULL).to_numpy()
    floor = float(config.bear_alloc_pct) / 100.0
    # The same returns the P&L is computed from, including on the first bar.
    # `pct_change` alone gives that bar a NaN, because on a trimmed frame its
    # predecessor was sliced off, and filling it with zero means the sleeve does
    # not grow on a day the portfolio is nevertheless credited for. One quantity,
    # two series: it read as a 0.03 % gap against a hand recomputation, with
    # MaxDD matching to the last decimal, which is the signature of a boundary
    # bar rather than a modelling difference. `trim_warmup` materialises
    # `prev_close` so that bar keeps the price it actually moved from.
    r = df["close"].pct_change()
    if "prev_close" in df.columns and pd.notna(df["prev_close"].iloc[0]):
        r.iloc[0] = df["close"].iloc[0] / df["prev_close"].iloc[0] - 1.0
    ret = r.fillna(0.0).to_numpy(float)
    n = len(bull)
    held = np.empty(n)
    traded = np.zeros(n)

    v_asset = 1.0 if bull[0] else floor
    v_cash = 0.0 if bull[0] else 1.0 - floor
    prev = bull[0]
    for i in range(n):
        if bull[i] != prev:
            total = v_asset + v_cash
            target = total if bull[i] else floor * total
            traded[i] = abs(target - v_asset) / total
            v_asset, v_cash = target, total - target
            prev = bull[i]
        total = v_asset + v_cash
        held[i] = v_asset / total
        v_asset *= (1.0 + ret[i])       # only the asset sleeve moves
    return held, traded


def position(df: pd.DataFrame, config: LeanConfig = DEFAULT_CONFIG) -> pd.Series:
    """Fraction of capital actually held in the asset, one number per bar.

    Position is YESTERDAY's signal. The strategy decides at a close and the
    earliest that decision can be held is the next bar, so `prev_signal_state`
    is the right column. Reading `signal_state` claims the move into the very
    close that produced the signal.

    With a floor above zero the series drifts rather than sitting at a constant,
    because the leftover holding is never rebalanced. See config.py for what the
    floor costs and why it is there.
    """
    return pd.Series(_sleeve_path(df, config)[0], index=df.index)


def traded_fraction(df: pd.DataFrame,
                    config: LeanConfig = DEFAULT_CONFIG) -> pd.Series:
    """Fraction of capital bought or sold on each bar.

    Pass this to `net_returns`. Using `turnover(position(...))` instead would
    bill every day of drift as a trade, which at a 5 % floor is a charge on
    something nobody does.
    """
    return pd.Series(_sleeve_path(df, config)[1], index=df.index)


