"""Stock data fetching and technical analysis engine.

Uses yfinance for real-time market data. All functions are synchronous
and designed to be called via asyncio.to_thread() from async contexts.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf


def fetch_quote(symbol: str) -> dict[str, Any]:
    """Fetch current quote and fundamental data for a stock symbol."""
    try:
        info = yf.Ticker(symbol).info
        return {
            "symbol": symbol.upper(),
            "name": info.get("longName") or info.get("shortName", symbol),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "currency": info.get("currency", "USD"),
            "change_pct": info.get("regularMarketChangePercent"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb_ratio": info.get("priceToBook"),
            "dividend_yield": info.get("dividendYield"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "analyst_target": info.get("targetMeanPrice"),
            "recommendation": info.get("recommendationKey"),
            "beta": info.get("beta"),
            "revenue_growth": info.get("revenueGrowth"),
            "gross_margins": info.get("grossMargins"),
            "earnings_growth": info.get("earningsGrowth"),
            "peg_ratio": info.get("pegRatio"),
            "short_float_pct": info.get("shortPercentOfFloat"),
        }
    except Exception as e:
        return {"symbol": symbol.upper(), "error": str(e)}


# ── Core indicator helpers ────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 2)


def _macd(close: pd.Series) -> dict[str, Any]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    hist = line - signal
    prev = hist.iloc[-2] if len(hist) >= 2 else 0.0
    cur = hist.iloc[-1]
    crossover = (
        "bullish_cross" if cur > 0 and prev <= 0 else
        "bearish_cross" if cur < 0 and prev >= 0 else
        "above_signal" if cur > 0 else "below_signal"
    )
    return {
        "macd": round(float(line.iloc[-1]), 4),
        "signal": round(float(signal.iloc[-1]), 4),
        "histogram": round(float(cur), 4),
        "crossover": crossover,
    }


def _bollinger(close: pd.Series, period: int = 20) -> dict[str, Any]:
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    cur = float(close.iloc[-1])
    upper_val = float(upper.iloc[-1])
    lower_val = float(lower.iloc[-1])
    position = (cur - lower_val) / (upper_val - lower_val) if upper_val != lower_val else 0.5
    return {
        "upper": round(upper_val, 2),
        "middle": round(float(ma.iloc[-1]), 2),
        "lower": round(lower_val, 2),
        "position": round(position, 2),
    }


def _ma_signals(close: pd.Series) -> dict[str, Any]:
    """MA50/200, golden/death cross detection."""
    result: dict[str, Any] = {}
    price = float(close.iloc[-1])
    if len(close) >= 50:
        ma50 = float(close.rolling(50).mean().iloc[-1])
        result["ma50"] = round(ma50, 2)
        result["price_vs_ma50"] = "above" if price > ma50 else "below"
    if len(close) >= 200:
        ma200 = float(close.rolling(200).mean().iloc[-1])
        result["ma200"] = round(ma200, 2)
        result["price_vs_ma200"] = "above" if price > ma200 else "below"
    if len(close) >= 201:
        ma50_s = close.rolling(50).mean()
        ma200_s = close.rolling(200).mean()
        prev_diff = float(ma50_s.iloc[-2]) - float(ma200_s.iloc[-2])
        curr_diff = float(ma50_s.iloc[-1]) - float(ma200_s.iloc[-1])
        if prev_diff <= 0 and curr_diff > 0:
            result["cross"] = "golden_cross (bullish)"
        elif prev_diff >= 0 and curr_diff < 0:
            result["cross"] = "death_cross (bearish)"
        else:
            result["cross"] = "none"
    return result


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict[str, Any]:
    """Average Directional Index — trend strength + direction."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    denom = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / denom
    adx_val = float(dx.ewm(alpha=alpha, adjust=False).mean().iloc[-1])

    return {
        "adx": round(adx_val, 2),
        "plus_di": round(float(plus_di.iloc[-1]), 2),
        "minus_di": round(float(minus_di.iloc[-1]), 2),
        "trend_strength": "strong (>25)" if adx_val > 25 else "weak/no trend (<25)",
        "trend_direction": "bullish" if float(plus_di.iloc[-1]) > float(minus_di.iloc[-1]) else "bearish",
    }


def _obv_trend(close: pd.Series, volume: pd.Series) -> dict[str, Any]:
    """On Balance Volume — volume confirms or diverges from price trend."""
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * volume).cumsum()
    obv_ma = obv.rolling(20).mean()
    recent_obv = float(obv.iloc[-1])
    ma_val = float(obv_ma.dropna().iloc[-1]) if not obv_ma.dropna().empty else recent_obv

    n = min(10, len(close) - 1)
    price_up = float(close.iloc[-1]) > float(close.iloc[-n])
    obv_up = recent_obv > float(obv.iloc[-n])
    divergence = (
        "bullish_divergence" if not price_up and obv_up else
        "bearish_divergence" if price_up and not obv_up else
        "confirming"
    )
    return {
        "obv": int(recent_obv),
        "obv_vs_ma20": "above" if recent_obv > ma_val else "below",
        "divergence": divergence,
    }


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict[str, Any]:
    """Average True Range — daily volatility in price terms."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_val = float(tr.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1])
    price = float(close.iloc[-1])
    return {
        "atr_14": round(atr_val, 4),
        "atr_pct_price": round(atr_val / price * 100, 2) if price else None,
    }


def _weekly_rsi(close_daily: pd.Series) -> float | None:
    """RSI calculated on weekly closes (resampled from daily data)."""
    try:
        weekly = close_daily.resample("W").last().dropna()
        if len(weekly) < 15:
            return None
        return _rsi(weekly)
    except Exception:
        return None


def _var_95(close: pd.Series) -> dict[str, Any]:
    """Historical Value at Risk (95%) and Conditional VaR."""
    rets = close.pct_change().dropna()
    if len(rets) < 30:
        return {"var_95_1d_pct": None}
    var_95 = float(rets.quantile(0.05))
    cvar_95 = float(rets[rets <= var_95].mean()) if (rets <= var_95).any() else var_95
    return {
        "var_95_1d_pct": round(var_95 * 100, 2),
        "cvar_95_1d_pct": round(cvar_95 * 100, 2),
        "interpretation": f"On 95% of days, loss ≤ {abs(round(var_95 * 100, 2))}% of position value",
    }


# ── Public fetch functions ────────────────────────────────────────────────────

def fetch_technical_signals(symbol: str, period: str = "1y") -> dict[str, Any]:
    """Fetch 1-year price history and compute comprehensive technical signals.

    Indicators: RSI-14, weekly RSI, MACD, Bollinger Bands, MA50/200 (golden/death
    cross), ADX, OBV, ATR, Value-at-Risk (95%), Sharpe ratio.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return {"symbol": symbol.upper(), "error": "no historical data"}

        close = hist["Close"]
        high  = hist["High"]
        low   = hist["Low"]
        vol   = hist["Volume"]

        sharpe = None
        if len(close) >= 252:
            rets = close.pct_change().dropna()
            std = rets.std()
            sharpe = round(float(rets.mean() / std * (252 ** 0.5)), 2) if std else None

        result: dict[str, Any] = {
            "symbol": symbol.upper(),
            "current_price": round(float(close.iloc[-1]), 2),
            "price_1m_change_pct": round(
                float((close.iloc[-1] / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0, 2
            ),
            "price_3m_change_pct": round(
                float((close.iloc[-1] / close.iloc[-66] - 1) * 100) if len(close) >= 66 else 0, 2
            ),
            "price_6m_change_pct": round(
                float((close.iloc[-1] / close.iloc[-132] - 1) * 100) if len(close) >= 132 else 0, 2
            ),
            "rsi_14": _rsi(close) if len(close) >= 14 else None,
            "weekly_rsi": _weekly_rsi(close),
            "macd": _macd(close) if len(close) >= 26 else None,
            "bollinger_20": _bollinger(close) if len(close) >= 20 else None,
            "ma_signals": _ma_signals(close) if len(close) >= 50 else None,
            "adx": _adx(high, low, close) if len(close) >= 28 else None,
            "obv": _obv_trend(close, vol) if len(close) >= 20 else None,
            "atr": _atr(high, low, close) if len(close) >= 14 else None,
            "var_95": _var_95(close),
            "avg_volume_10d": int(vol.tail(10).mean()) if len(vol) >= 10 else None,
            "sharpe_ratio_1y": sharpe,
        }
        return result
    except Exception as e:
        return {"symbol": symbol.upper(), "error": str(e)}


def fetch_options_sentiment(symbol: str) -> dict[str, Any]:
    """Fetch options market sentiment: put/call ratio, implied volatility.

    Uses yfinance options chain (no API key required). Returns the nearest
    expiration's put/call volume and open-interest ratios plus weighted-average
    implied volatility. A PCR > 1.2 is historically bearish; < 0.7 is bullish.
    """
    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return {"symbol": symbol.upper(), "error": "no options data available"}

        chain = ticker.option_chain(expirations[0])
        calls = chain.calls
        puts  = chain.puts

        call_vol = float(calls["volume"].fillna(0).sum()) if "volume" in calls.columns else 0.0
        put_vol  = float(puts["volume"].fillna(0).sum())  if "volume" in puts.columns  else 0.0
        pc_vol_ratio = round(put_vol / call_vol, 3) if call_vol > 0 else None

        call_oi = float(calls["openInterest"].fillna(0).sum()) if "openInterest" in calls.columns else 0.0
        put_oi  = float(puts["openInterest"].fillna(0).sum())  if "openInterest" in puts.columns  else 0.0
        pc_oi_ratio = round(put_oi / call_oi, 3) if call_oi > 0 else None

        all_opts = pd.concat([calls, puts], ignore_index=True)
        iv_avg = None
        if "impliedVolatility" in all_opts.columns and "openInterest" in all_opts.columns:
            iv_df = all_opts[["impliedVolatility", "openInterest"]].dropna()
            weights = iv_df["openInterest"].clip(lower=0)
            total_w = float(weights.sum())
            if total_w > 0:
                iv_avg = float((iv_df["impliedVolatility"] * weights).sum() / total_w)

        sentiment = (
            "bearish (PCR>1.2)" if pc_vol_ratio and pc_vol_ratio > 1.2 else
            "bullish (PCR<0.7)" if pc_vol_ratio and pc_vol_ratio < 0.7 else
            "neutral (PCR 0.7-1.2)"
        )

        return {
            "symbol": symbol.upper(),
            "nearest_expiry": expirations[0],
            "put_call_ratio_volume": pc_vol_ratio,
            "put_call_ratio_oi": pc_oi_ratio,
            "implied_vol_avg_pct": round(iv_avg * 100, 2) if iv_avg else None,
            "options_sentiment": sentiment,
        }
    except Exception as e:
        return {"symbol": symbol.upper(), "error": str(e)}


def rsi_interpretation(rsi: float | None) -> str:
    if rsi is None:
        return "N/A"
    if rsi >= 70:
        return f"Overbought ({rsi})"
    if rsi <= 30:
        return f"Oversold ({rsi})"
    return f"Neutral ({rsi})"
