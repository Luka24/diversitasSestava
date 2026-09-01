"""Configuration for Diversitas Lean — mirrors `diversitas_lean.pine` inputs.

Lean is intentionally smaller than Full: no conviction score, no ADX, no
weekly gate, no market structure. Bear regime is a hard block.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class LeanConfig:
    # Core trackline
    track_period: int = 75
    track_buf_pct: float = 3.0

    # Moving averages
    ma_long_len: int = 200   # regime MA, hard block when below + falling
    ma_slope: int = 5        # lookback bars for regime MA slope

    # Exits
    blowoff_dist_pct: float = 25.0
    # rsi_len is DELIBERATELY NOT A KNOB. RSI feeds only the blow-off detector,
    # where it is paired with a hard 80 threshold; sweeping the length while the
    # threshold stays fixed measures the interaction, not the rule. 14 is the
    # textbook value and is left at it so the blow-off rule has one free
    # parameter (blowoff_dist_pct) rather than two.
    rsi_len: int = 14

    # Range filter (kills sideways chop)
    track_slope_bars: int = 10

    # Anti-churn
    confirm_bars: int = 3
    reentry_hold: int = 15
    exit_grace_bars: int = 3

    # Sizing (additive, off the signal path)
    use_vol_sizing: bool = True
    target_vol_pct: float = 50.0

    # Permanent BTC floor. When the signal says out, this much stays invested,
    # so an exit sells 95 % rather than 100 %:
    #
    #     position = floor + (1 - floor) * signal
    #
    # A linear blend of buy-and-hold and the 0/100 strategy, not a new rule.
    # Set to 5 % on 2026-08-18.
    #
    # The measurement goes the other way, which is worth having findable. On BTC
    # from 2021-01-01 at 0.30 %/side, 2036 bars of the frozen Coinbase snapshot:
    #
    #      0 %   Sortino 1.476   27.8 % a year   28.6 % drawdown
    #      5 %           1.469   27.7 %          30.7 %
    #     10 %           1.456   27.6 %          32.8 %
    #
    # There is no level where a floor wins, because it is held through every
    # every rally. The full-window numbers do favour it, but that comes from
    # 2019 and 2020, when the drift was large enough that any permanent exposure
    # paid.
    #
    # It is a deliberate choice rather than a measured improvement: insurance
    # against the signal degrading. Blending the real signal with a random one
    # of the same frequency puts the break-even between 75 % and 100 % signal
    # quality. At 5 % both the cost and the cover are small.
    bear_alloc_pct: float = 5.0

    # NOTE: the Kaufman Efficiency Ratio gate (`use_er`, `er_len`, `er_thresh`) was
    # removed on 2026-07-27. It was a Python-only addition that Pine never had, and
    # it could not be shown to do anything: its threshold sat on the median of the ER
    # distribution, mean ER on BULL days (0.35) matched BEAR days (0.32), every
    # confidence interval spanned zero, and simply scaling positions to the same
    # average exposure matched or beat it on drawdown. See
    # `testing/porocilo_ER_lean.md` and `testing/porocilo_ER_BTC.html`.

    # NOTE: three rules and their four parameters were removed on 2026-08-03
    # (`min_dist_entry_pct`, `ma_med_len`, `vol_shock_mul`, `vol_lookback`; 14
    # tunable parameters → 10). Each was switched off and the position series came
    # back bit-identical on all 2700 bars — see `testing/data/reference_positions.*`
    # and `testing/tests/test_simplification.py`.
    #
    #   dist_entry_ok    arithmetic duplicate of `above_tl`. `above_tl` already
    #                    requires dist_pct > track_buf_pct, and with
    #                    min_dist_entry_pct = 0 the extra test asked for
    #                    dist_pct >= track_buf_pct. Alive at 0 of 151 settings.
    #   above_ma_med     blocked 65 days over the whole history; none of them would
    #                    have become a trade, because the other conditions blocked
    #                    them too. Alive at 3 of 151.
    #   vol_shock        fired on 0 of 21 exits — but it is NOT structurally dead.
    #                    It is inert at THESE FOUR VALUES specifically:
    #                    track_period 75 · track_buf_pct 3 % · exit_grace_bars 3 ·
    #                    reentry_hold 15. At exit_grace_bars = 2 it fires on 6 days,
    #                    and across the probe it woke at 67 of 151 settings. If any
    #                    of those four ever changes, this rule has to be re-measured
    #                    before it can be called dead. Probe:
    #                    `testing/scripts/dead_rules_robust.py`.

    # ENTRY GATE. On 2026-08-10 this replaced the old gate — close above the
    # 75-day trackline plus `track_buf_pct` — which is now used for the EXIT only.
    # Entry requires the close in the top quartile of the 20-day high/low channel.
    #
    # Written out, the Donchian condition is the same shape as the trackline it
    # replaced: close > midpoint(20d) + 0.25 x range(20d), against the old
    # close > midpoint(75d) + 3 % of price. Two differences — the lookback, and a
    # band measured in units of the range rather than as a fixed percentage, so it
    # widens when the market is wild and narrows when it is calm.
    #
    # Measured on BTC / ETH, net of 0.30 % per side:
    #   full window   Sortino 1.505 -> 1.936 and 1.078 -> 1.858
    #                 worst drawdown -45.2 % -> -30.2 % and -60.7 % -> -42.3 %
    #   from 2021     Sortino 1.321 -> 1.569 and 1.366 -> 1.876
    #                 worst drawdown -39.8 % -> -29.0 % and -42.8 % -> -42.3 %
    # Fewer trades and lower fees on both assets in both windows. It cleared the
    # nested walk-forward in both schemes, ETH out of sample, CSCV at 97.5 %, and
    # the circular-shift placebo at the 96th percentile.
    #
    # What it is NOT: a return improvement. On BTC the episode ledger is close to
    # even — seven favourable against eight unfavourable — and it wins on size, not
    # frequency. The confidence interval on BTC spans zero, it beats the old gate
    # in 2 of 4 sub-periods there, and PBO for choosing among the variants tested
    # was 0.711. Everything rests on 8 to 21 trades. It was adopted for the
    # mechanism — during a collapse the close cannot be in the top quartile of the
    # 20-day range — rather than for the numbers.
    #
    # Note also: it has changed nothing since 2024-12-15 on BTC and 2024-04-18 on
    # ETH. The last 18 months neither support nor contradict this.
    use_donchian: bool = True

    # 20 is the classic Donchian/Turtle short-term breakout length, chosen outside
    # this data. It is NOT to be tuned: PBO for selecting a period here was 0.672,
    # and the in-sample pick alternates between 12 and 40 — opposite ends of the
    # grid. The value in the rule is the rule, not the number.
    #
    # The previous default of 55 (the other Turtle length) sat in the WORST part of
    # the grid at Sortino 1.47, below the disabled state's 1.55, so anyone turning
    # the old flag on would have been misled.
    donchian_period: int = 20
    donchian_top_frac: float = 0.75

    # Optional cross-asset filter — OFF by default in Lean
    use_btc_filter: bool = False

    # Trading-day calendar: 365 for crypto (24/7), 252 for stock ETFs
    trading_days: int = 365

    # Symbol → per-source identifier (same map as full)
    # NOTE: the `coinbase` ids were missing here until 2026-07-27. Because this map
    # overrides `shared.data_source.DEFAULT_SYMBOL_MAP`, the Coinbase branch raised
    # "No Coinbase product" and was skipped — the fallback chain was silently
    # binance → yahoo, with no middle step and no indication in the UI.
    symbol_map: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "BTC": {"binance": "BTCUSDT", "coinbase": "BTC-USD", "yahoo": "BTC-USD", "coingecko": "bitcoin"},
        "ETH": {"binance": "ETHUSDT", "coinbase": "ETH-USD", "yahoo": "ETH-USD", "coingecko": "ethereum"},
        "SOL": {"binance": "SOLUSDT", "coinbase": "SOL-USD", "yahoo": "SOL-USD", "coingecko": "solana"},
        # Coinbase listed BNB on 2025-10-22, so it resolves now, but that is
        # only ~300 bars against the 220 the strategy needs before its first
        # signal. Short usable history, not a comparable one.
        "BNB": {"coinbase": "BNB-USD", "binance": "BNBUSDT", "yahoo": "BNB-USD", "coingecko": "binancecoin"},
        # HYPE lives on Hyperliquid's own venue. Not on Binance at all; Coinbase
        # and Kraken listed it in early 2026 and give under 210 bars where 220
        # are needed. Hyperliquid's API carries it from 2024-12-05, 627 bars.
        # No `yahoo` key ON PURPOSE: Yahoo's HYPE-USD is a different, dead token
        # from 2021 that ends at 0.0000.
        "HYPE": {"hyperliquid": "HYPE"},
        "XRP": {"binance": "XRPUSDT", "coinbase": "XRP-USD", "yahoo": "XRP-USD", "coingecko": "ripple"},
        "ADA": {"binance": "ADAUSDT", "coinbase": "ADA-USD", "yahoo": "ADA-USD", "coingecko": "cardano"},
        "AVAX": {"binance": "AVAXUSDT", "coinbase": "AVAX-USD", "yahoo": "AVAX-USD", "coingecko": "avalanche-2"},
        "LINK": {"binance": "LINKUSDT", "coinbase": "LINK-USD", "yahoo": "LINK-USD", "coingecko": "chainlink"},
        # ── equities / ETFs (yfinance only, 252 trading days/yr) ─────────────
        "SPY":  {"yahoo": "SPY"},    # S&P 500 ETF
        "QQQ":  {"yahoo": "QQQ"},    # Nasdaq-100 ETF
        "GLD":  {"yahoo": "GLD"},    # Gold ETF
    })


DEFAULT_CONFIG = LeanConfig()
