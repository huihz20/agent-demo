"""External market data sources beyond yfinance.

All functions degrade gracefully when API keys are absent — they return a dict
with a 'note' or 'error' key so the LLM skips the section and continues with
whatever data it has. Never raise from these functions.

Required env vars (add to .studio/.env.local):
    FRED_API_KEY            — https://fred.stlouisfed.org/docs/api/api_key.html (free)
    ALPHA_VANTAGE_API_KEY   — https://www.alphavantage.co/support/#api-key (free, 25 req/day)
    NEWS_API_KEY            — https://newsapi.org/register (free, 100 req/day)

SEC EDGAR is fully public — no key required. A User-Agent header is mandatory.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger("seller-agent.data_sources")

_EDGAR_HEADERS = {
    "User-Agent": "stockanalyst-agent contact@bnbchain.org",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT = 10


# ── Macro context (FRED + VIX) ────────────────────────────────────────────────

def fetch_macro_context() -> dict[str, Any]:
    """Fetch key macroeconomic indicators.

    Sources:
    - FRED (Federal Reserve Economic Data): fed funds rate, 10Y treasury yield,
      CPI year-over-year, unemployment rate.
    - yfinance: VIX (fear index) — no key needed.

    Returns a dict with numeric values and plain-English signals.
    """
    result: dict[str, Any] = {}
    api_key = os.environ.get("FRED_API_KEY", "")

    if api_key:
        try:
            from fredapi import Fred
            fred = Fred(api_key=api_key)
            result["fed_funds_rate"] = round(float(fred.get_series("FEDFUNDS").iloc[-1]), 2)
            result["treasury_10y_yield"] = round(float(fred.get_series("DGS10").iloc[-1]), 2)
            cpi = fred.get_series("CPIAUCSL")
            result["cpi_yoy_pct"] = round(float(cpi.pct_change(12).iloc[-1] * 100), 2)
            result["unemployment_pct"] = round(float(fred.get_series("UNRATE").iloc[-1]), 2)
            result["rate_environment"] = (
                "restrictive" if result["fed_funds_rate"] > 4 else
                "neutral" if result["fed_funds_rate"] > 2 else
                "accommodative"
            )
        except Exception as e:
            logger.warning("FRED fetch failed: %s", e)
            result["fred_error"] = str(e)
    else:
        result["fred_note"] = "FRED_API_KEY not set — macro data unavailable"

    # VIX from yfinance (no key required)
    try:
        import yfinance as yf
        vix_info = yf.Ticker("^VIX").info
        vix = vix_info.get("regularMarketPrice") or vix_info.get("previousClose")
        if vix:
            result["vix"] = round(float(vix), 2)
            result["vix_signal"] = (
                "extreme_fear (>30)"    if result["vix"] > 30 else
                "fear (20-30)"          if result["vix"] > 20 else
                "neutral (15-20)"       if result["vix"] > 15 else
                "complacency (<15)"
            )
    except Exception as e:
        logger.warning("VIX fetch failed: %s", e)

    return result


# ── SEC EDGAR — insider trading (Form 4) ─────────────────────────────────────

_CIK_CACHE: dict[str, str] = {}


def _get_cik(symbol: str) -> str | None:
    """Resolve a ticker symbol to its SEC CIK (zero-padded to 10 digits)."""
    key = symbol.upper()
    if key in _CIK_CACHE:
        return _CIK_CACHE[key]
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_EDGAR_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        for entry in resp.json().values():
            if str(entry.get("ticker", "")).upper() == key:
                cik = str(entry["cik_str"]).zfill(10)
                _CIK_CACHE[key] = cik
                return cik
    except Exception as e:
        logger.warning("CIK lookup failed for %s: %s", symbol, e)
    return None


def fetch_insider_trades(symbol: str, days: int = 90) -> dict[str, Any]:
    """Fetch recent insider Form 4 filings from SEC EDGAR.

    Form 4 is filed whenever a corporate insider (executive, director, or
    10%+ shareholder) buys or sells company stock. High filing frequency
    signals meaningful insider activity.

    Args:
        symbol: Ticker symbol (e.g. 'AAPL').
        days: Look-back window in calendar days.

    Returns a dict with filing count, dates, and an activity signal.
    """
    cik = _get_cik(symbol)
    if not cik:
        return {"symbol": symbol, "error": f"SEC CIK not found for {symbol}"}

    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_EDGAR_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])

        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        form4s = [
            dates[i]
            for i, f in enumerate(forms)
            if f in ("4", "4/A") and i < len(dates) and dates[i] >= cutoff
        ]

        return {
            "symbol": symbol,
            "period_days": days,
            "form4_filings": len(form4s),
            "recent_dates": form4s[:5],
            "activity_signal": (
                "high — 5+ filings suggest significant insider moves"   if len(form4s) >= 5 else
                "moderate — 2-4 filings, worth monitoring"              if len(form4s) >= 2 else
                "low — fewer than 2 Form 4s in the period"
            ),
        }
    except Exception as e:
        logger.warning("EDGAR Form 4 fetch failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e)}


# ── Alpha Vantage — AI-scored news sentiment ──────────────────────────────────

def fetch_alpha_vantage_sentiment(symbol: str) -> dict[str, Any]:
    """Fetch AI-scored news sentiment for a symbol from Alpha Vantage.

    Returns a sentiment score in [-1, 1] (negative = bearish, positive = bullish),
    a human-readable label, and the top headlines analysed.

    Requires ALPHA_VANTAGE_API_KEY in environment.
    Free tier: 25 requests/day.
    """
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        return {"symbol": symbol, "note": "ALPHA_VANTAGE_API_KEY not set"}

    try:
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "NEWS_SENTIMENT",
                "tickers": symbol,
                "limit": "20",
                "apikey": api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if "Information" in data:  # rate-limited
            return {"symbol": symbol, "note": data["Information"]}

        feed = data.get("feed", [])
        if not feed:
            return {"symbol": symbol, "article_count": 0}

        ticker_scores: list[float] = []
        headlines: list[str] = []
        for article in feed[:20]:
            for ts in article.get("ticker_sentiment", []):
                if ts.get("ticker", "").upper() == symbol.upper():
                    try:
                        ticker_scores.append(float(ts["ticker_sentiment_score"]))
                    except (KeyError, ValueError):
                        pass
            headlines.append(article.get("title", ""))

        avg = sum(ticker_scores) / len(ticker_scores) if ticker_scores else 0.0
        label = (
            "Bullish"          if avg >  0.15 else
            "Somewhat_Bullish" if avg >  0.05 else
            "Bearish"          if avg < -0.15 else
            "Somewhat_Bearish" if avg < -0.05 else
            "Neutral"
        )

        return {
            "symbol": symbol,
            "sentiment_score": round(avg, 3),
            "sentiment_label": label,
            "article_count": len(feed),
            "top_headlines": headlines[:5],
        }
    except Exception as e:
        logger.warning("Alpha Vantage sentiment failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e)}


# ── GNews — latest headlines ──────────────────────────────────────────────────

def fetch_gnews_headlines(symbol: str, company_name: str = "") -> dict[str, Any]:
    """Fetch the most relevant recent news headlines for a stock from GNews.io.

    Searches by company name (more precise than ticker). Returns raw titles
    so the LLM can incorporate them into its narrative.

    Requires GNEWS_API_KEY in environment.
    Free tier: 100 requests/day, up to 10 articles per request.
    Docs: https://gnews.io/docs/v4
    """
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        return {"symbol": symbol, "note": "GNEWS_API_KEY not set"}

    query = company_name or symbol
    try:
        resp = requests.get(
            "https://gnews.io/api/v4/search",
            params={
                "q": query,
                "lang": "en",
                "max": "5",
                "sortby": "relevance",
                "token": api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = data.get("articles", [])
        return {
            "symbol": symbol,
            "query": query,
            "total_results": data.get("totalArticles", len(articles)),
            "headlines": [
                {
                    "title": a.get("title", ""),
                    "source": (a.get("source") or {}).get("name", ""),
                    "published": (a.get("publishedAt") or "")[:10],
                }
                for a in articles
            ],
        }
    except Exception as e:
        logger.warning("GNews fetch failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e)}
