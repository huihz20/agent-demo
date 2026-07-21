"""Deterministic Markdown renderer for StockReport.

Takes a validated StockReport Pydantic model and emits clean, institutional-grade
Markdown. All formatting decisions are here — the LLM never touches layout.
Styled after sell-side equity research reports (Goldman Sachs / Morgan Stanley form).
"""
from __future__ import annotations

from report_schema import (
    ClientPosition,
    MacroSnapshot,
    PortfolioAction,
    RiskEntry,
    StockReport,
    StopLossEntry,
    SymbolAnalysis,
    WatchlistEntry,
)

# ── helpers ────────────────────────────────────────────────────────────────────

def _fmt_pct(v: float | None, plus: bool = False) -> str:
    if v is None:
        return "—"
    sign = "+" if plus and v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _fmt_float(v: float | None, decimals: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}{suffix}"


def _fmt_price(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


def _fmt_multiple(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}x"


def _rating_badge(rating: str) -> str:
    labels = {"Buy": "▲ BUY", "Hold": "◆ HOLD", "Sell": "▼ SELL"}
    return labels.get(rating, rating.upper())


def _risk_badge(level: str) -> str:
    stars = {"Low": "★☆☆☆", "Moderate": "★★☆☆", "High": "★★★☆", "Very High": "★★★★"}
    return f"{level} {stars.get(level, '')}"


# ── section renderers ──────────────────────────────────────────────────────────

def _render_header() -> str:
    return (
        "# EQUITY RESEARCH — PORTFOLIO ANALYSIS REPORT\n\n"
        "*AI-Powered Buy-Side Analysis · BNB Chain Platform · Powered by Kimi K2.6*\n\n"
        "---\n"
    )


def _render_investment_summary(report: StockReport) -> str:
    rows = []
    for a in report.analyses:
        rows.append(
            f"| **{a.symbol}** | {a.company_name} "
            f"| **{_rating_badge(a.rating)}** "
            f"| {_fmt_price(a.price_target)} "
            f"| {_fmt_price(a.current_price)} "
            f"| {_fmt_pct(a.implied_return_pct, plus=True)} "
            f"| {a.risk_level} |"
        )

    table = (
        "| Symbol | Company | Rating | Price Target | Current Price | Upside / Downside | Risk |\n"
        "|--------|---------|--------|-------------|---------------|-------------------|------|\n"
        + "\n".join(rows)
    )

    return (
        "## INVESTMENT SUMMARY\n\n"
        + table
        + "\n\n"
        + f"> {report.executive_summary}\n\n"
        + "---\n"
    )


def _render_macro(snap: MacroSnapshot) -> str:
    rows = [
        f"| VIX | {snap.vix} | {snap.vix_signal} |",
        f"| Fed Funds Rate | {snap.fed_rate} | {snap.fed_rate_signal} |",
        f"| 10-Year Treasury | {snap.treasury_10y} | {snap.treasury_10y_signal} |",
        f"| CPI (YoY) | {snap.cpi_yoy} | — |",
        f"| Unemployment | {snap.unemployment} | — |",
    ]
    table = (
        "| Indicator | Level | Signal |\n"
        "|-----------|-------|--------|\n"
        + "\n".join(rows)
    )
    return (
        "## MACRO ENVIRONMENT\n\n"
        + table
        + f"\n\n*{snap.macro_posture}*\n\n"
        + "---\n"
    )


def _render_fundamentals(a: SymbolAnalysis) -> str:
    def row(label: str, val: str, context: str = "") -> str:
        return f"| {label} | {val} | {context} |"

    target_str = "—"
    if a.analyst_target is not None:
        upside = f" ({_fmt_pct(a.analyst_upside_pct, plus=True)})" if a.analyst_upside_pct is not None else ""
        target_str = f"{_fmt_price(a.analyst_target)}{upside}"

    range_str = "—"
    if a.week_52_low is not None and a.week_52_high is not None:
        range_str = f"{_fmt_price(a.week_52_low)} – {_fmt_price(a.week_52_high)}"

    rows = [
        row("Current Price", _fmt_price(a.current_price)),
        row("52-Week Range", range_str),
        row("Market Cap", a.market_cap or "—"),
        row("Trailing P/E", _fmt_multiple(a.pe_trailing)),
        row("Forward P/E", _fmt_multiple(a.pe_forward)),
        row("PEG Ratio", _fmt_multiple(a.peg)),
        row("Analyst Consensus Target", target_str),
        row("Revenue Growth (YoY)", _fmt_pct(a.revenue_growth_pct, plus=True)),
        row("Gross Margin", _fmt_pct(a.gross_margin_pct)),
        row("Beta", _fmt_float(a.beta, 2)),
        row("Short Interest", _fmt_pct(a.short_float_pct)),
    ]
    table = (
        "| Metric | Value | Context |\n"
        "|--------|-------|---------|\n"
        + "\n".join(rows)
    )
    return (
        "#### Fundamental Snapshot\n\n"
        + table
        + f"\n\n*{a.fundamentals_commentary}*\n"
    )


def _render_technicals(a: SymbolAnalysis) -> str:
    def row(label: str, val: str, signal: str = "") -> str:
        return f"| {label} | {val} | {signal} |"

    ma_cross_str = a.ma_cross or "—"
    rows = [
        row("RSI-14", _fmt_float(a.rsi_14, 1), a.rsi_14_signal or "—"),
        row("RSI (Weekly)", _fmt_float(a.rsi_weekly, 1), a.rsi_weekly_signal or "—"),
        row("MACD", "—", a.macd_signal or "—"),
        row("Bollinger Position", _fmt_float(a.bollinger_position, 2), a.bollinger_signal or "—"),
        row("MA-50", _fmt_price(a.ma_50), "Price above" if (a.current_price and a.ma_50 and a.current_price > a.ma_50) else "Price below" if (a.current_price and a.ma_50) else "—"),
        row("MA-200", _fmt_price(a.ma_200), "Price above" if (a.current_price and a.ma_200 and a.current_price > a.ma_200) else "Price below" if (a.current_price and a.ma_200) else "—"),
        row("MA Cross", ma_cross_str, ""),
        row("ADX", _fmt_float(a.adx, 1), a.adx_signal or "—"),
        row("OBV Trend", "—", a.obv_trend or "—"),
        row("ATR (daily %)", _fmt_pct(a.atr_pct), "Daily volatility"),
        row("VaR (95%, 1-day)", _fmt_pct(a.var_95_pct), "Max daily loss, 95th percentile"),
    ]
    table = (
        "| Indicator | Reading | Signal |\n"
        "|-----------|---------|--------|\n"
        + "\n".join(rows)
    )
    return (
        "#### Technical Snapshot\n\n"
        + table
        + f"\n\n*{a.technicals_commentary}*\n"
    )


def _render_thesis(a: SymbolAnalysis) -> str:
    upside_items = "\n".join(f"{i+1}. {c}" for i, c in enumerate(a.upside_catalysts))
    risk_items = "\n".join(f"{i+1}. {r}" for i, r in enumerate(a.principal_risks))

    sentiment_lines = [f"*{a.sentiment_summary}*\n"]
    details = []
    if a.insider_activity:
        details.append(f"**Insider Activity:** {a.insider_activity}")
    if a.options_pcr is not None:
        pcr_str = f"{a.options_pcr:.2f}"
        iv_str = f" | **IV:** {a.implied_vol_pct:.1f}%" if a.implied_vol_pct is not None else ""
        details.append(f"**Options PCR:** {pcr_str}{iv_str}")
    if a.news_sentiment_score is not None:
        score_str = f"{a.news_sentiment_score:+.2f}"
        details.append(f"**News Sentiment Score:** {score_str}")
    if a.top_headline:
        details.append(f"**Top Headline:** \"{a.top_headline}\"")
    if details:
        sentiment_lines.extend(details)

    return (
        "#### Investment Thesis\n\n"
        "**Upside Case**\n\n"
        + upside_items
        + "\n\n**Principal Risks**\n\n"
        + risk_items
        + "\n\n**Sentiment Signals**\n\n"
        + "\n\n".join(sentiment_lines)
        + "\n"
    )


def _render_client_position(pos: ClientPosition) -> str:
    pnl_str = _fmt_pct(pos.unrealised_pnl_pct, plus=True)
    rows = [
        f"| Shares Held | {pos.shares:,.0f} |",
        f"| Average Cost | {_fmt_price(pos.avg_cost)} |",
        f"| Unrealised P&L | {pnl_str} |",
        f"| Stop-Loss Level | {_fmt_price(pos.stop_loss)} |",
        f"| Stop-Loss Basis | {pos.stop_loss_basis} |",
        f"| Recommended Action | {pos.action_summary} |",
    ]
    table = "| Item | Detail |\n|------|--------|\n" + "\n".join(rows)
    return "#### Client Position\n\n" + table + "\n"


def _render_symbol(a: SymbolAnalysis) -> str:
    header = (
        f"## {a.symbol} — {a.company_name}\n\n"
        f"### {_rating_badge(a.rating)} &nbsp;|&nbsp; "
        f"Price Target: {_fmt_price(a.price_target)} &nbsp;|&nbsp; "
        f"Implied Return: {_fmt_pct(a.implied_return_pct, plus=True)} &nbsp;|&nbsp; "
        f"Horizon: {a.horizon_months}mo &nbsp;|&nbsp; "
        f"Risk: {_risk_badge(a.risk_level)}\n\n"
        f"> {a.rating_rationale}\n\n"
    )
    parts = [
        header,
        _render_fundamentals(a),
        "\n",
        _render_technicals(a),
        "\n",
        _render_thesis(a),
    ]
    if a.client_position is not None:
        parts += ["\n", _render_client_position(a.client_position)]
    parts.append("\n---\n")
    return "\n".join(parts)


def _render_portfolio_actions(actions: list[PortfolioAction]) -> str:
    if not actions:
        return ""
    rows = [
        f"| {a.priority} | **{a.action}** | {a.symbol} | {a.quantity} "
        f"| {a.price_level} | {a.capital_impact} | {a.rationale} |"
        for a in sorted(actions, key=lambda x: x.priority)
    ]
    table = (
        "| Priority | Action | Symbol | Quantity | Price Level | Capital Impact | Rationale |\n"
        "|----------|--------|--------|----------|-------------|----------------|----------|\n"
        + "\n".join(rows)
    )
    return "## PORTFOLIO REBALANCING PLAN\n\n" + table + "\n\n---\n"


def _render_stop_losses(stops: list[StopLossEntry]) -> str:
    if not stops:
        return ""
    rows = [
        f"| {s.symbol} | {_fmt_price(s.avg_cost)} | {_fmt_price(s.stop_loss_level)} "
        f"| {_fmt_price(s.risk_per_share)} | {s.position_size} | {s.max_loss_at_stop} | {s.technical_basis} |"
        for s in stops
    ]
    table = (
        "| Symbol | Avg Cost | Stop Level | Risk / Share | Position | Max Loss at Stop | Basis |\n"
        "|--------|----------|------------|-------------|----------|------------------|-------|\n"
        + "\n".join(rows)
    )
    return "## STOP-LOSS SCHEDULE\n\n" + table + "\n\n---\n"


def _render_watchlist(entries: list[WatchlistEntry]) -> str:
    if not entries:
        return ""
    rows = [
        f"| **{e.ticker}** | {e.company} | {e.strategic_rationale} "
        f"| {e.key_catalyst} | {e.entry_zone} | {e.risk} |"
        for e in entries
    ]
    table = (
        "| Ticker | Company | Strategic Rationale | Key Catalyst | Entry Zone | Risk |\n"
        "|--------|---------|---------------------|-------------|------------|------|\n"
        + "\n".join(rows)
    )
    theses = "\n\n".join(
        f"**{e.ticker} — {e.company}:** {e.thesis}" for e in entries
    )
    return "## SECTOR WATCHLIST\n\n" + table + "\n\n" + theses + "\n\n---\n"


def _render_risk_dashboard(factors: list[RiskEntry]) -> str:
    if not factors:
        return ""
    rows = [
        f"| {f.factor} | **{f.assessment}** | {f.supporting_observation} | {f.threshold_to_act} |"
        for f in factors
    ]
    table = (
        "| Risk Factor | Assessment | Key Observation | Threshold to Act |\n"
        "|-------------|------------|-----------------|------------------|\n"
        + "\n".join(rows)
    )
    return "## RISK DASHBOARD\n\n" + table + "\n\n---\n"


def _render_disclaimer() -> str:
    return (
        "## DISCLAIMER\n\n"
        "*This report is prepared for informational purposes only and does not constitute "
        "personalised investment advice or a solicitation to buy or sell any security. "
        "Information is derived from publicly available data and algorithmic analysis; "
        "accuracy and completeness are not guaranteed. Past performance is not indicative "
        "of future results. All investments involve risk, including possible loss of principal. "
        "Recipients should conduct independent due diligence and consult a licensed financial "
        "adviser before acting on any information contained in this report.*\n"
    )


# ── public API ─────────────────────────────────────────────────────────────────

def render_report(report: StockReport) -> str:
    """Render a validated StockReport to institutional-grade Markdown."""
    sections = [
        _render_header(),
        _render_investment_summary(report),
        _render_macro(report.macro_snapshot),
    ]
    for analysis in report.analyses:
        sections.append(_render_symbol(analysis))
    sections += [
        _render_portfolio_actions(report.portfolio_actions),
        _render_stop_losses(report.stop_losses),
        _render_watchlist(report.watchlist),
        _render_risk_dashboard(report.risk_factors),
        _render_disclaimer(),
    ]
    return "\n".join(s for s in sections if s)
