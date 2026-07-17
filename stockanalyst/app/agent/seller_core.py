"""Seller core — the a2a-free seller logic + background delivery machinery.

This is the protocol-neutral heart of the ERC-8183 seller: the two fixed-code
operations (``negotiate`` → signed quote; ``notify_funded`` → verify → ACK →
deliver in the background) plus the background-delivery bookkeeping (``is_busy``,
the spawn/run/sweep helpers). It imports NOTHING from ``a2a`` so it can back any
transport — the A2A executor (``executor.py``) inherits it and wraps it with the
a2a wire, and a non-A2A HTTP entrypoint can call it directly without dragging in
``a2a-sdk``.

    negotiate     → ``signing.sign_quote`` (rule-based price clamp + EIP-191 sign)
    notify_funded → ``signing.verify_signed_job`` (fast on-chain gate) → ACK at
                    once, then in the BACKGROUND: LLM work → ``signing.submit_result``

``notify_funded`` is the buyer's "I funded job X — please deliver" notification.
Because the work takes time, it does NOT block the caller: it verifies the funded
job synchronously (a couple of eth_calls) to ACK accepted/rejected, then runs the
slow LLM work + on-chain ``submit`` in a background asyncio task and returns
immediately. The buyer reads the deliverable back from the CHAIN (SUBMITTED /
``get_deliverable_url``) — the chain is the source of truth. While any background
delivery is in flight :meth:`is_busy` reports busy, which the transport feeds to
AgentCore's ``/ping`` as ``HEALTHY_BUSY`` so the scale-to-zero runtime stays warm
until the work lands (within the session max-lifetime).

ALL signing is FIXED code in ``signing.py`` — NEVER an LLM-callable tool (money
is never in the LLM; the LLM only produces the work text, via the ``run_work``
hook). On each notification the core also opportunistically sweeps OTHER funded
jobs assigned to this provider — the buyer-push fallback for jobs whose buyer
funded on-chain but never sent ``notify_funded`` (deduped against in-flight jobs).
Negotiate stays sweep-free so quotes are fast. A periodic Lambda poller — which
also covers the scale-to-zero cold window when no one is invoking — is the v2
robust path.

You own this file — specialise the work hook / dispatch, but keep signing OUT of
the LLM tool list.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import signing
from bnbagent_studio_core.erc8183.errors import SubmitPermanentlyUnsupportedError

logger = logging.getLogger("seller-agent.core")


def _env_seconds(name: str, default: int) -> float:
    """Read a positive timeout (seconds) from the env, falling back to ``default``."""
    try:
        v = float(os.environ.get(name, "") or default)
        return v if v > 0 else float(default)
    except ValueError:
        return float(default)


# Background-task ceilings. notify_funded ACKs immediately and delivers in a
# BACKGROUND task; AgentCore keeps the scale-to-zero microVM warm (HEALTHY_BUSY)
# while is_busy() is True. A delivery (LLM text + on-chain submit + IPFS pin)
# normally finishes in ~1-2 min, so these caps sit far above real work and only
# fire on a HANG (e.g. an unresponsive RPC) — without them a hung task keeps the
# VM pinned to its 8h max-lifetime, billing memory the whole time. A timed-out
# job is treated as TRANSIENT (not dropped): the funded job stays on-chain and a
# later sweep re-delivers it idempotently.
_JOB_DELIVERY_TIMEOUT_SECONDS = _env_seconds("NOTIFY_DELIVERY_TIMEOUT_SECONDS", 1800)
_SWEEP_TIMEOUT_SECONDS = _env_seconds("NOTIFY_SWEEP_TIMEOUT_SECONDS", 60)
_PREVERIFY_TIMEOUT_SECONDS = _env_seconds("NOTIFY_PREVERIFY_TIMEOUT_SECONDS", 30)


class SellerCore:
    """ERC-8183 seller core: negotiate + notify_funded, backed by signing.py.

    ``run_work(prompt, *, session_id) -> str`` is the LLM work hook (built in
    ``main.py`` from the ADK runner); it is called inside the background delivery
    (``notify_funded`` → ``_do_work_and_submit``) to produce the deliverable text.

    The core exposes ONLY the two paid, structured operations — there is no
    free-form chat operation. The transport is responsible for routing a request
    to :meth:`negotiate` / :meth:`notify_funded`; a request that names no
    structured operation must never trigger an LLM call or a paid action.
    """

    def __init__(self, *, run_work, generator: str, network: str | None = None) -> None:
        self._run_work = run_work
        self._generator = generator
        self._network = network or "bsc-testnet"
        # Background delivery bookkeeping (see notify_funded / is_busy):
        #  _tasks       — live background asyncio tasks (busy-status source).
        #  _inflight    — job ids in flight OR already terminally handled this
        #                 process (notify/sweep dedup; retained on success so a
        #                 slower sweep never re-delivers a just-submitted job).
        self._tasks: set[asyncio.Task] = set()
        self._inflight: set[int] = set()
        # Per-job UOMP gateway params extracted from the buyer's notify_funded data.
        # Keyed by job_id; consumed (popped) in _do_work_and_submit.
        self._job_gateways: dict[int, tuple[str, str]] = {}
        # Per-job UOMP portfolio context (holdings + risk profile) from the buyer.
        self._job_portfolios: dict[int, dict] = {}

    def is_busy(self) -> bool:
        """True while any background delivery is in flight.

        The transport feeds this to AgentCore's ``/ping`` (``HEALTHY_BUSY`` when
        busy) so the scale-to-zero runtime is not reaped on idle while work runs.
        """
        return bool(self._tasks)

    # -- skills ----------------------------------------------------------------
    async def negotiate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Rule-based quote → SDK ``NegotiationResult`` envelope (no LLM).

        The price is the FIXED list price from studio.toml, clamped to
        ``[min,max]`` BEFORE signing — a misconfigured or hostile request can
        never sign out of bounds. The buyer parses this envelope verbatim and
        anchors it on-chain via ``createJob`` + ``fund``.
        """
        request = data.get("request")
        if not isinstance(request, dict):
            request = {k: data[k] for k in ("task_description", "terms") if k in data}
        clamped = signing.clamp_price(signing.list_price())
        return signing.sign_quote(request, clamped)

    @staticmethod
    def _skills() -> list[str]:
        """The seller's two advertised skills."""
        return ["negotiate", "notify_funded"]

    async def notify_funded(self, data: dict[str, Any]) -> dict[str, Any]:
        """Buyer notification: "I funded job X — please deliver."

        Verify the funded job synchronously (a couple of eth_calls) to ACK
        accepted/rejected at once, then run the slow LLM work + on-chain
        ``submit`` in a BACKGROUND task and return IMMEDIATELY. The buyer reads the
        deliverable back from the CHAIN (SUBMITTED / ``get_deliverable_url``) —
        the chain is the source of truth (see buyer-push-protocol.md).

        An accepted notification also kicks a background sweep (deduped against
        in-flight jobs), so a buyer that funded but forgot to notify is still
        served while we're warm. A rejected / malformed notification spawns
        nothing.
        """
        raw = data.get("job_id")
        if raw is None or str(raw) == "":
            self._spawn(self._sweep())  # bare notify → just scan stragglers
            return {"status": "accepted", "note": "no job_id — scanning funded jobs in the background; poll the chain for results"}
        try:
            job_id = _parse_job_id(raw)
        except (TypeError, ValueError):
            return {"status": "rejected", "error": f"invalid job_id: {raw!r}"}

        # UOMP delivery: buyer passes its relay URL + token so the seller can
        # upload the report directly to the buyer's local gateway.
        gw_url = data.get("delivery_gateway_url")
        gw_tok = data.get("delivery_gateway_token")
        if gw_url and gw_tok:
            self._job_gateways[job_id] = (str(gw_url), str(gw_tok))
            logger.info("job %s: UOMP gateway delivery → %s", job_id, gw_url)

        # UOMP portfolio context: buyer passes holdings + risk profile for personalised analysis.
        portfolio = data.get("portfolio")
        risk_profile = data.get("risk_profile")
        if portfolio or risk_profile:
            self._job_portfolios[job_id] = {
                "portfolio": portfolio or [],
                "risk_profile": risk_profile or {},
            }
            logger.info("job %s: portfolio context received (%d holdings)", job_id, len(portfolio or []))

        verified = False
        try:
            # Off the event loop + time-bounded: a blocking RPC must not stall the
            # ack path. On timeout we fall through to accept-and-re-verify below.
            ok, reason, permanent = await asyncio.wait_for(
                asyncio.to_thread(signing.verify_signed_job, job_id),
                timeout=_PREVERIFY_TIMEOUT_SECONDS,
            )
            if not ok and permanent:
                logger.warning("job %s: verify rejected permanently — %s", job_id, reason)
                return {"status": "rejected", "job_id": job_id, "reason": reason}
            verified = ok
        except Exception as e:  # noqa: BLE001 — pre-verify is best-effort; bg re-verifies (incl. TimeoutError)
            logger.warning("pre-verify of job %s failed (%s); accepting, will re-verify in background", job_id, e)
        self._spawn_job(job_id, verified=verified)
        self._spawn(self._sweep())  # straggler fallback alongside the named job
        return {
            "status": "accepted",
            "job_id": job_id,
            "note": "delivery started; poll the chain (SUBMITTED / get_deliverable_url) for the result",
        }

    # -- background delivery ---------------------------------------------------
    def _spawn(self, coro: Any) -> None:
        """Run ``coro`` in a tracked background task (keeps :meth:`is_busy` True)."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _spawn_job(self, job_id: int, *, verified: bool) -> None:
        """Background-deliver ``job_id`` once, deduped against in-flight jobs.

        ``_inflight`` is updated SYNCHRONOUSLY here (before scheduling) so a
        concurrent notify + sweep can never double-deliver the same job.
        """
        if job_id in self._inflight:
            return
        self._inflight.add(job_id)
        self._spawn(self._run_job(job_id, verified=verified))

    async def _run_job(self, job_id: int, *, verified: bool) -> None:
        """Background runner: deliver one job, log the outcome, free the slot.

        ``verified`` jobs (pre-verified in ``notify_funded``) skip straight to the
        work; unverified ones (the sweep) run the full verify gate first.
        """
        terminal = False
        try:
            # Hard ceiling so a hung delivery (e.g. unresponsive RPC) cannot keep
            # is_busy() True — which would pin the microVM to its 8h max-lifetime.
            # A timeout is TRANSIENT: terminal stays False, the slot is freed, and
            # the funded job is re-delivered idempotently by a later sweep.
            result = await asyncio.wait_for(
                self._do_work_and_submit(job_id) if verified else self._fulfill_job(job_id),
                timeout=_JOB_DELIVERY_TIMEOUT_SECONDS,
            )
            logger.info("notify_funded job %s → %s", job_id, result)
            # A terminal outcome (delivered, or a permanent skip) must STAY in
            # _inflight: keeping it lets the dedup gate in _spawn_job reject a
            # slower concurrent sweep that still sees this job as FUNDED, so the
            # just-submitted job is never re-delivered. Clearing on success
            # reopened that race — the sweep re-ran the work and then failed the
            # on-chain FUNDED gate (Job status is SUBMITTED). Only transient
            # failures fall through to discard so a later sweep can retry them.
            terminal = bool(result.get("ok") or result.get("skip"))
        except (asyncio.TimeoutError, TimeoutError):
            # Transient by design — leave terminal False so a later sweep retries.
            logger.warning(
                "background delivery of job %s timed out after %ss; will retry",
                job_id,
                _JOB_DELIVERY_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — a background job must never crash the loop
            logger.exception("background delivery of job %s failed", job_id)
        finally:
            if not terminal:
                self._inflight.discard(job_id)

    # -- internals -------------------------------------------------------------
    async def _fulfill_job(self, job_id: int) -> dict[str, Any]:
        """Verify the signed deal on-chain, then deliver (the sweep's per-job worker).

        VERIFY before working: confirm the funded job carries the exact quote
        THIS agent signed (ecrecover + budget ≥ price). A permanent failure
        (not our signature, tampered terms, underfunded, expired) returns
        ``skip: True``; a transient one returns ``ok: False`` to retry.
        """
        ok, reason, permanent = await asyncio.to_thread(signing.verify_signed_job, job_id)
        if not ok:
            return {"ok": False, "job_id": job_id, "skip": permanent, "reason": reason}
        return await self._do_work_and_submit(job_id)

    async def _do_work_and_submit(self, job_id: int) -> dict[str, Any]:
        """LLM work → sign + submit. Assumes ``job_id`` is already verified.

        DEVELOPER HOOK: the LLM block produces the deliverable text — specialise
        it for your seller. ``signing.submit_result`` re-runs the SDK ``verify_job``
        (defense in depth) and RAISES on a failed submit, so an ``ok: True`` result
        always carries a landed tx hash.
        """
        # Pop gateway params (if the buyer sent them in notify_funded). These are
        # consumed once here and not retained — each notify_funded is independent.
        gateway = self._job_gateways.pop(job_id, None)
        gateway_url, gateway_token = gateway if gateway else (None, None)

        # Pop UOMP portfolio context (if the buyer sent it in notify_funded).
        portfolio_data = self._job_portfolios.pop(job_id, {})

        spec = await asyncio.to_thread(signing.job_spec, job_id)
        if spec is not None:
            task = json.dumps({"task": spec.task, "terms": spec.terms}, ensure_ascii=False)
        else:
            task = f"job {job_id}"
        prompt = _build_stock_analysis_prompt(
            task,
            portfolio=portfolio_data.get("portfolio", []),
            risk_profile=portfolio_data.get("risk_profile", {}),
        )
        work = await self._run_work(prompt, session_id=str(job_id))

        try:
            res = await asyncio.to_thread(
                signing.submit_result,
                job_id,
                response_content=work,
                metadata={
                    "job_id": job_id,
                    "generator": self._generator,
                    "built_with": "https://github.com/bnb-chain/bnbagent-studio",
                },
                gateway_url=gateway_url,
                gateway_token=gateway_token,
            )
        except SubmitPermanentlyUnsupportedError as e:
            # Deterministic for this wallet kind: submit can NEVER succeed →
            # permanent skip (a transient error would burn one LLM call / retry).
            return {"ok": False, "job_id": job_id, "skip": True, "reason": str(e)}
        return {
            "ok": True,
            "job_id": job_id,
            "tx_hash": res.submit_tx,
            "deliverable_url": res.deliverable_url,
        }

    async def _sweep(self) -> None:
        """Best-effort background fallback: deliver any FUNDED jobs for this provider.

        Catches jobs whose buyer funded on-chain but never sent ``notify_funded``.
        Each job is handed to ``_spawn_job`` (deduped against in-flight jobs, so a
        concurrent notify never double-delivers); ``verify_signed_job`` returns
        non-OK for an already-SUBMITTED job (idempotent, no state file). Errors
        here are logged and never surface to the caller.
        """
        try:
            from bnbagent.erc8183 import ERC8183JobOps

            from bnbagent_studio_core.wallet import get_wallet

            ops = ERC8183JobOps(wallet_provider=get_wallet(), network=self._network)
            # Time-bounded: a hung scan would otherwise keep is_busy() True (it runs
            # on every notify) and pin the microVM to its 8h max-lifetime.
            pending = await asyncio.wait_for(ops.get_pending_jobs(), timeout=_SWEEP_TIMEOUT_SECONDS)
        except Exception as e:  # noqa: BLE001 — the sweep is best-effort (incl. TimeoutError)
            logger.warning("funded-job sweep failed: %s", e)
            return
        for job in (pending or {}).get("jobs", []):
            jid = job.get("jobId") if isinstance(job, dict) else None
            if jid is None:
                continue
            try:
                self._spawn_job(int(jid), verified=False)
            except (TypeError, ValueError):
                continue


def _build_stock_analysis_prompt(
    task_json: str,
    portfolio: list | None = None,
    risk_profile: dict | None = None,
) -> str:
    """Build a comprehensive stock analysis prompt from the job task JSON and UOMP context."""
    try:
        data = json.loads(task_json)
        task_desc = data.get("task", "")
        terms = data.get("terms", {})
        if isinstance(terms, str):
            try:
                terms = json.loads(terms)
            except Exception:
                terms = {}
    except Exception:
        task_desc = task_json
        terms = {}

    symbols = terms.get("symbols") or []
    analysis_type = terms.get("analysis_type", "comprehensive")
    language = terms.get("language", "en")

    if not symbols and task_desc:
        import re
        found = re.findall(r'\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b', task_desc)
        symbols = list(dict.fromkeys(found))[:10]

    symbol_list = ", ".join(symbols) if symbols else "the requested stocks"
    lang_instruction = (
        "Respond in Chinese (中文)." if language in ("zh", "zh-CN", "zh-TW")
        else "Respond in English."
    )

    # Build portfolio context block if we have holdings
    portfolio_block = ""
    holding_map: dict[str, dict] = {}
    if portfolio:
        lines = ["CLIENT PORTFOLIO (use this for personalised P&L analysis):"]
        for h in portfolio:
            sym = str(h.get("symbol", "")).upper()
            avg_cost = h.get("avgCost")
            shares = h.get("shares")
            currency = h.get("currency", "USD")
            if sym and avg_cost is not None and shares is not None:
                holding_map[sym] = {"avgCost": avg_cost, "shares": shares, "currency": currency}
                lines.append(f"  {sym}: {shares} shares @ {currency} {avg_cost:.2f} avg cost")
        if len(lines) > 1:
            portfolio_block = "\n".join(lines)

    risk_block = ""
    if risk_profile:
        tolerance = risk_profile.get("tolerance", "moderate")
        horizon = risk_profile.get("horizonMonths", 12)
        indicators = risk_profile.get("preferredIndicators", [])
        parts = [f"CLIENT RISK PROFILE: {tolerance} tolerance, {horizon}mo horizon"]
        if indicators:
            parts.append(f"  Preferred indicators: {', '.join(indicators)}")
        risk_block = "\n".join(parts)

    context_section = "\n".join(filter(None, [portfolio_block, risk_block]))
    if context_section:
        context_section = f"\n{context_section}\n"

    # Build explicit per-symbol checklist for Stage 1 and the report outline for Stage 2
    symbol_checklist = "\n".join(
        f"  {i+1}. {sym}: get_stock_quote, get_technical_signals, get_options_sentiment, "
        f"get_insider_activity, get_news_sentiment"
        for i, sym in enumerate(symbols)
    ) if symbols else f"  1. {symbol_list}: all five tools"

    symbol_report_outline = "\n\n---\n\n".join(
        f"## {sym} — [Full Company Name]\n\n"
        "[Write the complete per-stock block for this symbol as described below.]"
        for sym in (symbols if symbols else [symbol_list])
    )

    return f"""You are a senior equity analyst at a top-tier investment bank. A client has paid for a professional research report.

STOCKS TO ANALYZE: {symbol_list}
NUMBER OF STOCKS: {len(symbols) if symbols else 1} — you must produce a complete analysis block for EACH one.
ANALYSIS TYPE: {analysis_type}
{lang_instruction}{context_section}

════════════════════════════════════════════════════════
STAGE 1 — COLLECT ALL DATA (complete every call before writing)
════════════════════════════════════════════════════════
Call all five tools for EACH symbol, then call get_macro_context() once:

{symbol_checklist}
  + get_macro_context()  (once only)

Do not begin writing until every tool call above has returned a result.

════════════════════════════════════════════════════════
STAGE 2 — WRITE THE RESEARCH REPORT
════════════════════════════════════════════════════════
MANDATORY RULES:
1. Every number, percentage, and price must be taken verbatim from a tool call result. Do not estimate or recall from memory.
2. Do not leave any placeholder in the final output. If a tool returned no data for a field, write a dash (—).
3. Do not use emoji or decorative icons anywhere in the report.
4. Write in formal institutional prose for analysis sections. Use tables for all structured data.
5. Every recommendation must name a specific price level, share count, or percentage trigger.
6. You MUST write a complete analysis block for every symbol listed above. Do not stop after the first stock.

---

# Stock Analysis Report

## Executive Summary

Write this section FIRST, after all tool calls are complete.
Three to five sentences covering:
- Overall market posture (risk-on or risk-off, based on VIX and rate data)
- A one-line verdict for EACH stock (Rating, Price Target, key reason in one clause)
- The single most important portfolio action the client should take this week

---

## Market Snapshot

Write a five-row table: Indicator | Value | Signal.
Use exact values from get_macro_context().
Rows: VIX, Fed Funds Rate (or 3-month T-bill rate if Fed Funds unavailable), 10-Year Treasury Yield, CPI YoY (write "—" if unavailable), Unemployment Rate (write "—" if unavailable).
Signal column: one concise phrase per indicator (e.g. "Low volatility — risk appetite supportive").
Follow with one sentence on overall macro posture and its implication for the portfolio.

---

{symbol_report_outline}

For EACH stock block above, write the following sections in full:

**Rating: [Buy / Hold / Sell]** | **Price Target: $[price]** | **Implied Return: [+/-%]** | **Horizon: [N] months** | **Risk: [Low / Moderate / High / Very High]**

Two to three sentences of rating rationale before the tables.

**Rating: [Buy / Hold / Sell]** | **Price Target: $[price]** | **Implied Return: [+/-%]** | **Horizon: [N] months** | **Risk: [Low / Moderate / High / Very High]**

State the rating rationale in two to three sentences of institutional prose before the tables. Reference the key factor driving the rating — whether valuation, technical setup, catalyst timing, or risk asymmetry.

### Fundamentals

Write a table with columns Metric | Value | Context.
Rows: Current Price, 52-Week Range (low – high), Market Cap, Trailing P/E, Forward P/E, PEG Ratio, Analyst Consensus Target (and implied upside/downside %), Revenue Growth (YoY %), Gross Margin (%), Beta, Short Interest (% of float).
Context column: note whether each metric is elevated, in-line, or depressed relative to the stock's own history or sector median. Be specific (e.g. "Forward P/E of 24x sits at a 15% premium to the sector median of 21x").

### Technical Analysis

Write a table with columns Indicator | Reading | Signal.
Rows: RSI-14, RSI (Weekly), MACD, Bollinger Band Position (0.0 = lower band, 1.0 = upper band), MA-50, MA-200, MA Cross (Golden / Death / None), ADX, OBV Trend, ATR (% of price), 1-Day VaR (95%).
Signal column: state the regime implication in plain language (e.g. RSI-14 of 68 = "approaching overbought — momentum intact but limited headroom").

### Investment Thesis

**Upside Case**
Enumerate three specific, time-bound catalysts that support the rating. Each point must identify a mechanism, an expected timeframe, and — where the data supports it — a magnitude. Write as numbered points in formal prose, not bullet fragments.

**Principal Risks**
Enumerate three specific risks that could invalidate the thesis. Each point must identify a trigger event or price level and the expected impact on the stock. Write as numbered points in formal prose.

**Sentiment Indicators**
One paragraph covering insider transaction activity (Form 4 data: net buyer/seller, number of transactions), options market positioning (put/call ratio, implied volatility vs 30-day historical), news sentiment score and direction, and the single most relevant recent headline. Reference the exact figures returned by the tools.

### Client Position
(Include this section only if the client holds this stock. Omit entirely if not held.)

Write a two-column summary table (Item | Detail) with rows: Shares Held, Average Cost, Current Price, Unrealised P&L ($ and %), Gain to Price Target ($ and % on position), Recommended Stop-Loss (price and % below current, with one-line rationale).

---

## Portfolio Rebalancing

Assess the full client portfolio and provide specific, sequenced instructions. If recommending a new position, name which existing holding to reduce in order to fund it.

### Current Allocation

Write a table: Symbol | Shares | Avg Cost | Current Price | Current Value | Unrealised P&L % | Portfolio Weight % | Assessment.
Compute Current Value as shares multiplied by current price from tool results.
Assessment column: Overweight / Fair Weight / Underweight, with one clause explaining why.

### Recommended Actions

Write a prioritised action table: Priority | Action | Symbol | Quantity | Price | Capital Impact | Rationale.
Action column values: Trim / Add / New Buy / Hold.
Rationale must reference a specific technical or fundamental finding from the analysis above — not generic language.
Below the table, write one sentence on net capital impact (cash freed by trims vs cash required for buys) and one sentence on how portfolio concentration changes after execution.

### Stop-Loss Schedule

Write a table: Symbol | Entry / Avg Cost | Stop-Loss Level | Risk Per Share | Position Size | Max Loss at Stop.
Stop-loss levels must be derived from a technical level identified in the analysis (support, moving average, ATR-based) — state which.

---

## Watchlist

Using your equity market knowledge, recommend three to five stocks not currently held that are strategically relevant to this portfolio — sector peers, supply-chain adjacencies, thematic complements, or potential hedges. For each name:

Write a table row: Ticker | Company | Strategic Rationale | Key Catalyst | Attractive Entry Zone | Risk.
Then write two sentences of investment thesis explaining how this name relates to the client's existing positions and what the risk/reward profile looks like at the entry zone.

---

## Risk Summary

Write a table: Risk Factor | Assessment | Threshold to Act.
Rows: Sector Concentration (note actual % in the largest sector), Rate Sensitivity (beta-weighted), Inter-Holding Correlation, Portfolio VaR (aggregate 1-day 95%), Liquidity Risk (based on ATR and average daily volume).
Assessment column: Low / Moderate / High with one specific supporting observation.
Threshold to Act column: name the level or event that would warrant a portfolio adjustment.

---

## Disclaimer
This report is prepared for informational purposes only and does not constitute personalised investment advice or a solicitation to buy or sell any security. The information herein is derived from publicly available data and algorithmic analysis; it is not guaranteed as to accuracy or completeness. Past performance is not indicative of future results. All investments carry risk, including the possible loss of principal. Recipients should conduct independent due diligence and consult a licensed financial adviser before acting on any information contained in this report.
"""


def _parse_job_id(raw: Any) -> int:
    """Normalise an envelope ``job_id`` (``0x..`` / decimal string / int) to int."""
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)
