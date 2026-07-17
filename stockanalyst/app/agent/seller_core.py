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
_JOB_DELIVERY_TIMEOUT_SECONDS = _env_seconds("NOTIFY_DELIVERY_TIMEOUT_SECONDS", 600)
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

    return f"""You are a professional buy-side portfolio analyst. A client has paid for a premium, actionable report.
Your goal: help this investor make confident, well-reasoned decisions — not just describe data.

STOCKS TO ANALYZE: {symbol_list}
ANALYSIS TYPE: {analysis_type}
{lang_instruction}{context_section}

════════════════════════════════════════════════════════
STAGE 1 — COLLECT ALL DATA FIRST (do NOT write until done)
════════════════════════════════════════════════════════
For EACH symbol call ALL of:
  get_stock_quote(symbol), get_technical_signals(symbol), get_options_sentiment(symbol),
  get_insider_activity(symbol), get_news_sentiment(symbol)
Call once: get_macro_context()

════════════════════════════════════════════════════════
STAGE 2 — WRITE THE REPORT
════════════════════════════════════════════════════════
Rules: use tables wherever possible, lead with verdict, be direct and specific.
NEVER fabricate any number — use only data from tool calls.
Every recommendation must name a specific price target, share count, or threshold.

---

# Stock Analysis Report

## 📊 Market Snapshot

| Indicator | Value | Signal |
|-----------|-------|--------|
| VIX | [value] | <20 Calm / 20-30 Caution / >30 Fear |
| Fed Funds Rate | [value]% | Direction + impact on equities |
| 10Y Treasury | [value]% | Bond yield pressure on growth stocks |
| CPI YoY | [value]% | Inflation trajectory |
| Unemployment | [value]% | Labour market health |

**Overall:** [one sentence: risk-on or risk-off environment, and what it means for the portfolio]

---

## [SYMBOL] — [Company Name]

> ### Verdict: **[BUY / HOLD / SELL]** | Target: $[X] ([+/-X%] upside) | Horizon: [X]mo | Risk: ★★★☆☆

### Fundamentals
| Metric | Value | Context |
|--------|-------|---------|
| Price | $[X] | — |
| 52W Range | $[lo] – $[hi] | Position within range |
| Market Cap | $[X]B | — |
| PE (trailing/fwd) | [X] / [X] | vs sector avg |
| PEG Ratio | [X] | <1 undervalued, >1 stretched |
| Analyst Target | $[X] ([+/-X%]) | Consensus view |
| Revenue Growth | [X]% | Trend direction |
| Gross Margin | [X]% | Quality indicator |
| Beta | [X] | Portfolio volatility contribution |
| Short Float | [X]% | Squeeze risk |

### Technical Signals
| Indicator | Reading | Signal |
|-----------|---------|--------|
| RSI-14 | [X] | Oversold <30 / Neutral 30-70 / Overbought >70 |
| RSI (weekly) | [X] | Longer-term momentum |
| MACD | [value] | Bullish cross / Bearish cross / Neutral |
| Bollinger Position | [X] (0=lower, 1=upper) | Near band or midline |
| MA50 / MA200 | $[X] / $[X] | Golden cross / Death cross / Neutral |
| ADX | [X] | >25 trending, <20 ranging |
| OBV Trend | [up/down/flat] | Accumulation or distribution |
| ATR (daily risk) | [X]% | Expected daily move |
| VaR 95% (1-day) | -[X]% | Worst-case 1-day loss (95% confidence) |

### Investment Case

**🐂 Bull Thesis** — reasons to buy:
1. [specific catalyst with expected timing]
2. [specific catalyst with expected timing]
3. [specific catalyst with expected timing]

**🐻 Bear Risks** — reasons to exit:
1. [specific risk and trigger level]
2. [specific risk and trigger level]
3. [specific risk and trigger level]

**Sentiment signals:** Insider activity [high/moderate/low] | Options PCR [X] ([bullish/neutral/bearish]), IV [X]% | News sentiment [+/-X.X] ([label]) | Headline: "[top headline]"

### Your Position ← omit this section entirely if client does not hold this stock
| | |
|-|-|
| Shares held | [N] @ avg cost $[X] |
| Current price | $[X] |
| Unrealised P&L | [+/-$X] ([+/-X%]) |
| At target $[X] | Potential additional gain: [+$X] ([+X%] on position) |
| Suggested stop-loss | $[X] ([-X%] from current) — exit if thesis breaks |

(repeat the above per-stock block for each symbol)

---

## 🔄 Portfolio Rebalancing Plan

Assess the full portfolio and give SPECIFIC, actionable instructions. If you recommend buying a stock, name which existing holding(s) to trim to fund it. Be concrete: share counts and approximate dollar amounts.

### Current Allocation
| Symbol | Shares | Avg Cost | ~Current Value | Unrealised P&L | Est. Weight | Status |
|--------|--------|----------|---------------|----------------|-------------|--------|
| [symbol] | [N] | $[X] | $[X] | [+/-X%] | [X%] | Overweight / Fair / Underweight |

### Recommended Actions (in priority order)
| # | Action | Symbol | Quantity | ~Price | ~Proceeds/Cost | Rationale |
|---|--------|--------|----------|--------|---------------|-----------|
| 1 | TRIM | [symbol] | Sell [N] shares | $[X] | ~$[X] freed | [e.g. overbought RSI, near resistance, funds higher-conviction position] |
| 2 | ADD / BUY | [symbol] | Buy [N] shares | $[X] | ~$[X] | [e.g. pullback to support, strong ADX, below analyst target] |
| 3 | HOLD | [symbol] | — | — | — | [e.g. technically neutral, await next catalyst] |

**Net capital impact:** Trims free ~$[X] | New buys cost ~$[X] | Net [cash in / cash out]: ~$[X]
**After rebalance:** [Brief note on how the portfolio concentration and risk profile changes]

### Stop-Loss Summary
| Symbol | Avg Cost / Entry | Stop-Loss Level | Risk Per Share | Est. Max Loss |
|--------|-----------------|-----------------|----------------|--------------|
| [symbol] | $[X] | $[X] | $[X] | ~$[X] total |

---

## 🔭 Watchlist: Related Stocks Worth Monitoring

Using your market knowledge, suggest 3–5 stocks that are sector peers, thematic peers, or correlated names relevant to the client's positions. These are NOT current holdings — they are future opportunities to track.

| Symbol | Company | Why It's Relevant | Key Catalyst to Watch | Attractive Entry Zone | Risk Level |
|--------|---------|-------------------|-----------------------|-----------------------|------------|
| [ticker] | [name] | [e.g. direct competitor, same supply chain, sector beneficiary] | [e.g. earnings date, product launch, regulatory event] | $[lo]–$[hi] | Low / Med / High |

For each watchlist name, add 1–2 sentences explaining the investment thesis and how it relates to the client's existing positions (complementary, hedge, or higher-beta alternative).

---

## ⚠️ Risk Dashboard

| Risk Factor | Level | What to Watch |
|-------------|-------|--------------|
| Sector concentration | [High/Med/Low] | [% in one sector — flag if >50%] |
| Rate sensitivity | [High/Med/Low] | [Beta-weighted rate impact] |
| Stock correlation | [High/Med/Low] | [Do holdings move together? Diversification benefit?] |
| Downside (portfolio VaR) | [X%] | [Combined 1-day 95% VaR estimate] |
| Liquidity | [High/Med/Low] | [Volume and ATR — ability to exit quickly] |

---

## Disclaimer
This report is for informational purposes only and does not constitute personalised investment advice. Past performance does not guarantee future results. All investments carry risk, including the possible loss of principal. Do your own research and consult a licensed financial adviser before making investment decisions.
"""


def _parse_job_id(raw: Any) -> int:
    """Normalise an envelope ``job_id`` (``0x..`` / decimal string / int) to int."""
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)
