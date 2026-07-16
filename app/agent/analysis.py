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
        }
    except Exception as e:
        return {"symbol": symbol.upper(), "error": str(e)}


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
    u, l = float(upper.iloc[-1]), float(lower.iloc[-1])
    position = (cur - l) / (u - l) if u != l else 0.5
    return {
        "upper": round(u, 2),
        "middle": round(float(ma.iloc[-1]), 2),
        "lower": round(l, 2),
        "position": round(position, 2),
    }


def fetch_technical_signals(symbol: str, period: str = "6mo") -> dict[str, Any]:
    """Fetch price history and compute RSI, MACD, and Bollinger Bands."""
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist.empty:
            return {"symbol": symbol.upper(), "error": "no historical data"}
        close = hist["Close"]
        vol = hist["Volume"]
        sharpe = None
        if len(close) >= 252:
            rets = close.pct_change().dropna()
            sharpe = round(float(rets.mean() / rets.std() * (252 ** 0.5)), 2)
        return {
            "symbol": symbol.upper(),
            "current_price": round(float(close.iloc[-1]), 2),
            "price_1m_change_pct": round(
                float((close.iloc[-1] / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0, 2
            ),
            "price_3m_change_pct": round(
                float((close.iloc[-1] / close.iloc[-66] - 1) * 100) if len(close) >= 66 else 0, 2
            ),
            "rsi_14": _rsi(close) if len(close) >= 14 else None,
            "macd": _macd(close) if len(close) >= 26 else None,
            "bollinger_20": _bollinger(close) if len(close) >= 20 else None,
            "avg_volume_10d": int(vol.tail(10).mean()) if len(vol) >= 10 else None,
            "sharpe_ratio_1y": sharpe,
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
