"""Crypto candle fetching — Binance primary, Coinbase + yfinance fallbacks.

Public API of this module:
    fetch_candles(symbol, interval='1d', bars=500) -> pd.DataFrame
        Returns UTC-indexed DataFrame with columns [open, high, low, close, volume].

Source ordering (per call):
    1. Binance public REST  (no key, 6000 weight/min)
    2. Coinbase Advanced    (no key, 10 req/sec, deepest history from 2015,
                             US-friendly when Binance is geo-blocked)
    3. yfinance              (no key, ~15 min latency, daily candles only)

Rationale documented in API_REPORT.md.

Symbol resolution: pass `config` (any object exposing a `.symbol_map` dict)
or rely on the built-in DEFAULT_SYMBOL_MAP fallback. This keeps the module
config-agnostic — both `full` and `lean` Diversitas variants share it.
"""
from __future__ import annotations
import time
import warnings
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd
import requests


# Default symbol → per-source identifier mapping. Both LeanConfig and (Full)
# Config also carry this map; callers can override by passing `config`.
DEFAULT_SYMBOL_MAP: Mapping[str, Mapping[str, str]] = {
    "BTC": {"binance": "BTCUSDT", "coinbase": "BTC-USD", "yahoo": "BTC-USD", "coingecko": "bitcoin"},
    "ETH": {"binance": "ETHUSDT", "coinbase": "ETH-USD", "yahoo": "ETH-USD", "coingecko": "ethereum"},
    "SOL": {"binance": "SOLUSDT", "coinbase": "SOL-USD", "yahoo": "SOL-USD", "coingecko": "solana"},
    # BNB is not listed on Coinbase — leave the key out so fallback skips it.
    "BNB": {"binance": "BNBUSDT", "yahoo": "BNB-USD", "coingecko": "binancecoin"},
    "XRP": {"binance": "XRPUSDT", "coinbase": "XRP-USD", "yahoo": "XRP-USD", "coingecko": "ripple"},
    "ADA": {"binance": "ADAUSDT", "coinbase": "ADA-USD", "yahoo": "ADA-USD", "coingecko": "cardano"},
    "AVAX": {"binance": "AVAXUSDT", "coinbase": "AVAX-USD", "yahoo": "AVAX-USD", "coingecko": "avalanche-2"},
    "LINK": {"binance": "LINKUSDT", "coinbase": "LINK-USD", "yahoo": "LINK-USD", "coingecko": "chainlink"},
    # ── equities / ETFs (yfinance only, 252 trading days/yr) ─────────────────
    "SPY": {"yahoo": "SPY"},   # S&P 500 ETF
    "QQQ": {"yahoo": "QQQ"},   # Nasdaq-100 ETF
    "GLD": {"yahoo": "GLD"},   # Gold ETF
}


def _resolve_symbol_map(config: Any) -> Mapping[str, Mapping[str, str]]:
    """Accept either a Config object (with .symbol_map) or None for default."""
    if config is None:
        return DEFAULT_SYMBOL_MAP
    sm = getattr(config, "symbol_map", None)
    if sm is None:
        return DEFAULT_SYMBOL_MAP
    return sm


BINANCE_URL = "https://api.binance.com/api/v3/klines"

# `data-api.binance.vision` is Binance's own market-data-only host: no account,
# no auth, no trading endpoints. Because it serves nothing but candles it is not
# behind the jurisdiction check that makes `api.binance.com` answer
#
#     HTTP 451 {"code": 0, "msg": "Service unavailable from a restricted
#                                  location according to 'b. Eligibility'..."}
#
# from some networks. Verified 2026-08-11 to return byte-identical klines to
# api.binance.com for BTCUSDT 1d. It is a second address for the same data, not
# a way around the restriction — the restriction is on trading, and we only ever
# read prices.
BINANCE_HOSTS = (
    "https://api.binance.com/api/v3/klines",
    "https://data-api.binance.vision/api/v3/klines",
)
COINBASE_URL = "https://api.exchange.coinbase.com/products/{pid}/candles"

# Logical interval -> per-source code / granularity.
# Coinbase only supports a fixed set of granularities (seconds):
#   60 / 300 / 900 / 3600 / 21600 / 86400. We mark unsupported as None.
_INTERVAL_MAP = {
    "1d": {"binance": "1d", "yf": "1d",  "coinbase_sec": 86400},
    "1w": {"binance": "1w", "yf": "1wk", "coinbase_sec": None},  # caller resamples
    "4h": {"binance": "4h", "yf": "1h",  "coinbase_sec": None},  # 14400 not supported
    "1h": {"binance": "1h", "yf": "1h",  "coinbase_sec": 3600},
}


class DataSourceError(RuntimeError):
    pass


def _binance_get(params: dict) -> "requests.Response":
    """One klines call, trying each Binance host in turn.

    A 451 is a property of the host, not of the request, so retrying the same
    address is pointless — but the data-only host answers where the trading host
    will not. Rate limits (418/429) are NOT retried on the second host: that
    would be the same account-less caller hammering the same exchange.

    Network-level failures move to the next host too. A block is often a reset
    connection or a poisoned DNS answer rather than a polite 451, and an
    exception that escaped here would skip the host that actually works.

    The raised error names EVERY host and what it answered. Reporting only the
    last one made "the second host is blocked too" and "the second host was
    never tried" produce the same message, which is exactly the ambiguity that
    kept this bug alive.
    """
    tried: list[str] = []
    for i, url in enumerate(BINANCE_HOSTS):
        host = url.split("/")[2]
        try:
            r = requests.get(url, params=params, timeout=15)
        except Exception as e:  # noqa: BLE001 — connection reset, DNS, timeout
            tried.append(f"{host} -> {type(e).__name__}: {str(e)[:90]}")
            continue
        if r.status_code == 200:
            if i:
                r.headers["X-Diversitas-Host"] = url    # for the diagnostic
            return r
        if r.status_code in (418, 429):
            raise DataSourceError(
                f"Binance rate limit hit on {host} (HTTP {r.status_code}) — "
                f"back off and retry")
        tried.append(f"{host} -> HTTP {r.status_code}: "
                     f"{' '.join(r.text.split())[:110]}")
    raise DataSourceError(
        f"Binance: vsi gostitelji ({len(BINANCE_HOSTS)}) so odpovedali  ||  "
        + "  ||  ".join(tried))


def _binance_fetch(symbol_binance: str, interval: str, bars: int) -> pd.DataFrame:
    """Fetch up to `bars` recent candles from Binance.

    Binance's `limit` max is 1000. For larger requests we paginate backwards
    using `endTime`.
    """
    per_call = 1000
    remaining = bars
    chunks: list[pd.DataFrame] = []
    end_time: Optional[int] = None

    while remaining > 0:
        params = {
            "symbol": symbol_binance,
            "interval": interval,
            "limit": min(per_call, remaining),
        }
        if end_time is not None:
            params["endTime"] = end_time
        r = _binance_get(params)
        raw = r.json()
        if not raw:
            break
        df_chunk = _binance_parse(raw)
        chunks.append(df_chunk)
        remaining -= len(df_chunk)
        # next page: end at the open of the earliest candle minus 1 ms
        first_open_ms = int(raw[0][0])
        end_time = first_open_ms - 1
        if len(raw) < params["limit"]:
            break
        time.sleep(0.05)  # be polite

    if not chunks:
        raise DataSourceError(f"Binance returned no candles for {symbol_binance}")

    df = pd.concat(chunks[::-1]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.tail(bars)


def _binance_parse(raw: list[list]) -> pd.DataFrame:
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(raw, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df.set_index("open_time")[["open", "high", "low", "close", "volume"]]
    df.index.name = "time"
    return df


def _coinbase_fetch(product_id: str, interval: str, bars: int) -> pd.DataFrame:
    """Fetch up to `bars` recent candles from Coinbase Advanced Trade.

    The endpoint is `/products/{id}/candles` (max 300 candles/call). We
    paginate backwards via the `end` parameter using the oldest timestamp
    of the previous page minus one granularity step.

    Coinbase returns candles **newest first**, as arrays
    `[time_sec, low, high, open, close, volume]`.

    Only supports granularities listed in `_INTERVAL_MAP[interval]['coinbase_sec']`;
    raises DataSourceError for unsupported intervals (caller's source loop
    will fall through to the next provider).
    """
    gran = _INTERVAL_MAP[interval]["coinbase_sec"]
    if gran is None:
        raise DataSourceError(
            f"Coinbase does not support interval {interval!r} natively"
        )

    per_call = 300
    remaining = bars
    chunks: list[pd.DataFrame] = []
    end_ts: Optional[int] = None  # epoch seconds

    headers = {"User-Agent": "diversitas/1.0"}

    while remaining > 0:
        params: dict = {"granularity": gran}
        if end_ts is not None:
            # end is inclusive on Coinbase — step one granularity back
            end_dt = pd.Timestamp(end_ts, unit="s", tz="UTC")
            start_dt = end_dt - pd.Timedelta(seconds=gran * per_call)
            params["start"] = start_dt.isoformat()
            params["end"] = end_dt.isoformat()

        r = requests.get(
            COINBASE_URL.format(pid=product_id),
            params=params, headers=headers, timeout=15,
        )
        if r.status_code == 429:
            raise DataSourceError("Coinbase rate limit hit (HTTP 429)")
        if r.status_code != 200:
            raise DataSourceError(
                f"Coinbase HTTP {r.status_code}: {r.text[:200]}"
            )
        raw = r.json()
        if not isinstance(raw, list) or not raw:
            break

        df_chunk = _coinbase_parse(raw)
        chunks.append(df_chunk)
        remaining -= len(df_chunk)
        # next page: end = earliest_returned - 1 second
        earliest_ts = int(min(row[0] for row in raw))
        end_ts = earliest_ts - 1
        if len(raw) < per_call:
            break
        time.sleep(0.1)  # be polite (10 req/sec IP cap)

    if not chunks:
        raise DataSourceError(
            f"Coinbase returned no candles for {product_id}"
        )

    df = pd.concat(chunks).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.tail(bars)


def _coinbase_parse(raw: list[list]) -> pd.DataFrame:
    """Coinbase candle order: [time_sec, low, high, open, close, volume]."""
    cols = ["time_sec", "low", "high", "open", "close", "volume"]
    df = pd.DataFrame(raw, columns=cols)
    df["time"] = pd.to_datetime(df["time_sec"].astype("int64"), unit="s", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
    return df


def _yahoo_direct_fetch(symbol_yf: str, bars: int) -> pd.DataFrame:
    """Yahoo Finance v8 chart API via plain requests — no websockets needed.

    Yahoo has required a crumb cookie since 2023. We seed a session by
    visiting finance.yahoo.com, fetch the crumb, then query the chart API.
    """
    import time as _time
    from urllib.parse import quote as _quote

    period2 = int(_time.time())
    period1 = period2 - max(bars * 2, 400) * 86400

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # Step 1: seed session cookie
    try:
        session.get("https://finance.yahoo.com", timeout=8)
    except Exception:
        pass

    # Step 2: obtain crumb (required for authenticated API calls)
    crumb = ""
    for crumb_url in (
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
    ):
        try:
            rc = session.get(crumb_url, timeout=8)
            if rc.status_code == 200 and rc.text.strip():
                crumb = rc.text.strip()
                break
        except Exception:
            continue

    # Step 3: fetch chart data (^ must be URL-encoded in the path)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{_quote(symbol_yf)}"
    params: dict = {
        "interval": "1d",
        "period1": period1,
        "period2": period2,
        "events": "div,splits",
    }
    if crumb:
        params["crumb"] = crumb

    r = session.get(url, params=params, timeout=15)
    if r.status_code != 200:
        raise DataSourceError(f"Yahoo Finance HTTP {r.status_code}: {r.text[:200]}")

    data = r.json()
    result = (data.get("chart") or {}).get("result")
    if not result:
        err = (data.get("chart") or {}).get("error") or "empty response"
        raise DataSourceError(f"Yahoo Finance no data for {symbol_yf}: {err}")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quotes = (chart.get("indicators") or {}).get("quote", [{}])[0]

    if not timestamps:
        raise DataSourceError(f"Yahoo Finance: no timestamps for {symbol_yf}")

    df = pd.DataFrame({
        "open":   quotes.get("open",   [None] * len(timestamps)),
        "high":   quotes.get("high",   [None] * len(timestamps)),
        "low":    quotes.get("low",    [None] * len(timestamps)),
        "close":  quotes.get("close",  [None] * len(timestamps)),
        "volume": quotes.get("volume", [0]    * len(timestamps)),
    }, index=pd.to_datetime(timestamps, unit="s", utc=True))
    df.index.name = "time"
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.tail(bars)


def _yf_fetch(symbol_yf: str, interval: str, bars: int) -> pd.DataFrame:
    """yfinance fallback using yf.download() — avoids the websockets code path."""
    import yfinance as yf

    period_days = max(bars + 30, 60)
    period = "max" if period_days > 730 else f"{period_days}d"
    yf_interval = _INTERVAL_MAP.get(interval, _INTERVAL_MAP["1d"])["yf"]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(
            symbol_yf,
            period=period,
            interval=yf_interval,
            auto_adjust=False,
            progress=False,
            multi_level_index=False,
        )

    if df.empty:
        raise DataSourceError(f"yfinance returned empty for {symbol_yf}")

    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df = df[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    return df.tail(bars)


def _hyperliquid_fetch(coin: str, interval: str, bars: int) -> pd.DataFrame:
    """Fetch daily candles from Hyperliquid's own API.

    Here for one reason: HYPE. Hyperliquid is a DEX and its token does not trade
    on Binance at all, while Coinbase and Kraken only listed it in early 2026.
    Both give under 210 daily bars, and the strategy needs 220 before it can
    produce a single signal, so the asset was not merely thin, it was
    uncomputable. Hyperliquid's own endpoint carries it from 2024-12-05, which
    is 627 bars and 407 usable ones.

    Checked against the 200 days Coinbase does have: mean price difference
    0.052 %, worst 0.24 %. Same asset, not the dead Yahoo token of the same
    ticker that ends at zero.

    POST /info with type=candleSnapshot. Returns oldest-first dicts with string
    numbers: t (open ms), o, h, l, c, v.
    """
    if interval != "1d":
        raise DataSourceError(
            f"Hyperliquid fetcher only implements '1d', not {interval!r}")

    out: dict[int, dict] = {}
    start = 1_700_000_000_000          # comfortably before the token existed
    for _ in range(20):                # bounded, so a bad reply cannot spin
        try:
            r = requests.post(
                "https://api.hyperliquid.xyz/info", timeout=15,
                json={"type": "candleSnapshot",
                      "req": {"coin": coin, "interval": "1d",
                              "startTime": start, "endTime": 4_000_000_000_000}},
            )
            r.raise_for_status()
            page = r.json()
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"Hyperliquid request failed for {coin}: {e}") from e
        if not page:
            break
        for k in page:
            out[int(k["t"])] = k
        nxt = int(page[-1]["t"]) + 86_400_000
        if nxt <= start or len(page) < 2:
            break
        start = nxt

    if not out:
        raise DataSourceError(f"Hyperliquid returned no candles for {coin}")

    rows = [out[k] for k in sorted(out)]
    df = pd.DataFrame(
        {"open":   [float(x["o"]) for x in rows],
         "high":   [float(x["h"]) for x in rows],
         "low":    [float(x["l"]) for x in rows],
         "close":  [float(x["c"]) for x in rows],
         "volume": [float(x["v"]) for x in rows]},
        index=pd.to_datetime([int(x["t"]) for x in rows], unit="ms", utc=True),
    )
    df.index.name = "time"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.tail(bars)


def fetch_candles(
    symbol: str,
    interval: str = "1d",
    bars: int = 500,
    config: Any = None,
    prefer: str = "binance",
    strict: bool = False,
) -> pd.DataFrame:
    """Public entry point.

    Args:
        symbol: logical symbol, e.g. 'BTC', 'ETH'. Must be in symbol_map.
        interval: '1d', '1w', '4h', '1h'.
        bars: number of most-recent candles to return.
        config: any object exposing `.symbol_map` (e.g. Config, LeanConfig).
                Pass `None` to use the built-in DEFAULT_SYMBOL_MAP.
        prefer: which source to try FIRST. Accepts 'binance' (default),
                'coinbase', or 'yahoo'. The other two are tried in order
                as fallbacks if the preferred one fails.
        strict: when True, do NOT fall back — raise if `prefer` fails.

    Returns:
        DataFrame indexed by UTC timestamp, columns [open, high, low, close, volume].
        `df.attrs["source"]` names the venue the data actually came from.

    Why `strict` exists: the venues agree on price to ~0.1 %, but the strategy's
    entry is a *threshold* (`close > trackline × 1.03`). When price sits next to
    it, a tenth of a percent decides whether a trade fires today, three days
    later, or not at all — measured at 3 percentage points of CAGR between
    Binance and Yahoo on BTC. A silent fallback therefore silently changes every
    number on the page. Anything whose output gets written down should pass
    `strict=True`.
    """
    symbol_map = _resolve_symbol_map(config)
    symbol = symbol.upper()
    if symbol not in symbol_map:
        raise ValueError(
            f"Unknown symbol {symbol!r}. Known: {sorted(symbol_map)}"
        )
    if interval not in _INTERVAL_MAP:
        raise ValueError(f"Unsupported interval {interval!r}")

    # Source ordering: primary first, then fallbacks. `prefer` can override
    # the primary; we always try Coinbase before yfinance because Coinbase is
    # a real exchange (Yahoo is a scraper).
    if prefer == "hyperliquid":
        # Single-asset venue with no sensible partner: an asset that lives here
        # lives only here, so it never shares a chain and never falls through.
        sources = ["hyperliquid"]
    elif prefer == "yahoo":
        sources = ["yahoo", "binance", "coinbase"]
    elif prefer == "coinbase":
        sources = ["coinbase", "binance", "yahoo"]
    else:  # default / "binance"
        sources = ["binance", "coinbase", "yahoo"]
    if strict:
        sources = sources[:1]

    def _one(src: str) -> pd.DataFrame:
        if src == "binance":
            if "binance" not in symbol_map[symbol]:
                raise DataSourceError(f"No Binance id for {symbol}")
            return _binance_fetch(symbol_map[symbol]["binance"],
                                  _INTERVAL_MAP[interval]["binance"], bars)
        if src == "coinbase":
            if "coinbase" not in symbol_map[symbol]:
                raise DataSourceError(f"No Coinbase product for {symbol}")
            return _coinbase_fetch(symbol_map[symbol]["coinbase"], interval, bars)
        if src == "hyperliquid":
            if "hyperliquid" not in symbol_map[symbol]:
                raise DataSourceError(f"No Hyperliquid coin for {symbol}")
            return _hyperliquid_fetch(symbol_map[symbol]["hyperliquid"], interval, bars)
        if src == "yahoo":
            if "yahoo" not in symbol_map[symbol]:
                raise DataSourceError(f"No Yahoo ticker for {symbol}")
            ticker_yf = symbol_map[symbol]["yahoo"]
            # Try direct HTTP first (no websockets dependency), then yfinance.
            try:
                return _yahoo_direct_fetch(ticker_yf, bars)
            except Exception:
                return _yf_fetch(ticker_yf, interval, bars)
        raise DataSourceError(f"Unknown source {src!r}")

    last_err: Optional[Exception] = None
    for src in sources:
        try:
            df = _one(src)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        df.attrs["source"] = src          # so callers can report what they got
        return df
    if strict:
        raise DataSourceError(
            f"{symbol} {interval}: source {sources[0]!r} failed and strict=True, so no "
            f"fallback was used. Falling back to another venue would silently change "
            f"every number computed from this data. Original error: {last_err}")
    raise DataSourceError(f"All sources failed for {symbol} {interval}: {last_err}")


def fetch_btc_daily(bars: int = 500, config: Any = None) -> pd.DataFrame:
    """Convenience: BTC daily for the cross-asset filter."""
    return fetch_candles("BTC", "1d", bars=bars, config=config)


def fetch_spx_daily(bars: int = 500) -> pd.Series:
    """S&P 500 (^GSPC) daily close prices, timezone-naive index.

    Tries yf.download first (no websockets code path), then the direct
    HTTP API as a fallback.  Returns a Series named 'spx'.
    """
    import yfinance as yf

    period_days = max(bars + 30, 60)
    period = "max" if period_days > 730 else f"{period_days}d"

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download("^GSPC", period=period, interval="1d",
                             auto_adjust=True, progress=False,
                             multi_level_index=False)
        if not df.empty:
            s = df["Close"].tail(bars).copy()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            s.name = "spx"
            return s
    except Exception:
        pass

    # Fallback: direct HTTP (may need crumb)
    df = _yahoo_direct_fetch("^GSPC", bars)
    s = df["close"].copy()
    if s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    s.name = "spx"
    return s


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (Mon-anchored, label = Monday).

    Used for the macro filters (weekly EMA/SMA/close).
    """
    rule = "W-MON"
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    return daily.resample(rule, closed="left", label="left").agg(agg).dropna()
