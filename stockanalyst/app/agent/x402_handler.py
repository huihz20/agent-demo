"""x402 HTTP payment channel with SSE streaming delivery.

Adds three routes alongside the existing A2A server (pure ASGI middleware —
no extra framework, no new heavy deps):

  GET  /x402/price              → 200 JSON: current price + asset info (no payment)
  GET  /x402/analyze?symbols=.. → 402 JSON + X-Payment-Required header (challenge)
  POST /x402/analyze            → with X-Payment header → 200 SSE stream

Request body (POST, JSON):
  {
    "symbols": ["AAPL", "NVDA"],          # required — list or comma-string
    "analysis_type": "comprehensive"       # optional (default: "comprehensive")
  }

SSE event stream format (one event per line-pair):
  event: progress
  data: {"stage": "collecting", "tool": "get_stock_quote", "message": "Running get_stock_quote..."}

  event: progress
  data: {"stage": "generating", "message": "Rendering final report..."}

  event: report
  data: {"content": "# EQUITY RESEARCH...", "format": "markdown"}

  event: error
  data: {"message": "..."}

  event: done
  data: {}

Payment verification is FIXED CODE in x402_verify.py — never LLM-callable.
The stream_work callable is the ADK runner generator from main.py (also not LLM-priced;
it runs fixed code to collect data then calls the LLM for text only).

For dual-channel operation: ERC-8183 (trustless escrow, on-chain settlement) runs
alongside x402 (simpler Binance Pay flow, off-chain, lower friction for quick buys).
Both channels share the same LLM analysis pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.parse
from typing import Any, AsyncGenerator, Callable

from x402_verify import build_payment_challenge, verify_payment_proof

logger = logging.getLogger("seller-agent.x402")


class X402Handler:
    """ASGI middleware: intercepts /x402/* routes, forwards everything else.

    Mount as the outermost ASGI layer in main.py so it sits in front of the
    A2A server, the ERC-8183 local-storage route, and the JSON-RPC error-
    hardening middleware.

    Args:
        app: Inner ASGI application (A2A + existing routes).
        stream_work: Async generator factory — called as
            ``stream_work(prompt, session_id, symbols)`` and yields
            ``(event_name: str, data: dict)`` tuples. Defined in main.py
            (has access to the ADK runner + report parser/renderer); never
            imported here to avoid circular imports.
        generator: Agent identity string logged in SSE delivery metadata.
    """

    def __init__(
        self,
        app,
        *,
        stream_work: Callable[..., AsyncGenerator[tuple[str, dict], None]],
        generator: str,
    ) -> None:
        self._inner = app
        self._stream_work = stream_work
        self._generator = generator

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/x402"):
            await self._inner(scope, receive, send)
            return

        path = scope["path"].rstrip("/")
        method = (scope.get("method") or "GET").upper()

        if path == "/x402/price":
            await self._handle_price(scope, send)
        elif path == "/x402/analyze" and method == "GET":
            await self._handle_challenge(scope, send)
        elif path == "/x402/analyze" and method == "POST":
            await self._handle_analyze(scope, receive, send)
        else:
            await _send_json(send, 404, {"error": "not found", "x402_routes": [
                "GET  /x402/price",
                "GET  /x402/analyze?symbols=AAPL,NVDA",
                "POST /x402/analyze  (+ X-Payment header)",
            ]})

    # ── Route handlers ─────────────────────────────────────────────────────────

    async def _handle_price(self, scope, send) -> None:
        """GET /x402/price — price info without payment."""
        challenge = build_payment_challenge([], "")
        await _send_json(send, 200, {
            "price_u": "1.0",
            "price_wei": challenge["maxAmountRequired"],
            "min_price_u": "0.5",
            "min_price_wei": str(5 * 10**17),
            "asset": challenge["asset"],
            "network": challenge["network"],
            "payTo": challenge["payTo"],
            "signingMessage": challenge["extra"]["signingMessage"],
        })

    async def _handle_challenge(self, scope, send) -> None:
        """GET /x402/analyze?symbols=... — return 402 payment challenge."""
        qs = urllib.parse.parse_qs((scope.get("query_string") or b"").decode())
        symbols_raw = (qs.get("symbols") or [""])[0]
        symbols = _parse_symbols(symbols_raw)
        host = _host(scope)
        challenge = build_payment_challenge(symbols, host)
        challenge_json = json.dumps(challenge).encode()
        await send({
            "type": "http.response.start",
            "status": 402,
            "headers": [
                (b"x-payment-required", challenge_json),
                (b"content-type", b"application/json"),
            ],
        })
        body = json.dumps({
            "error": "Payment Required",
            "description": "Include a valid X-Payment header to access this resource.",
            "paymentRequired": challenge,
        }).encode()
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _handle_analyze(self, scope, receive, send) -> None:
        """POST /x402/analyze — verify payment, stream SSE report."""
        # Read request body
        chunks: list[bytes] = []
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                chunks.append(msg.get("body") or b"")
                if not msg.get("more_body"):
                    break

        try:
            req: dict[str, Any] = json.loads(b"".join(chunks)) if chunks else {}
        except json.JSONDecodeError:
            await _send_json(send, 400, {"error": "invalid JSON body"})
            return

        # Parse symbols
        symbols = _parse_symbols(req.get("symbols") or "")
        if not symbols:
            await _send_json(send, 400, {
                "error": "symbols is required",
                "example": '{"symbols": ["AAPL", "NVDA"]}',
            })
            return

        headers_dict: dict[bytes, bytes] = dict(scope.get("headers") or [])
        payment_header = (headers_dict.get(b"x-payment") or b"").decode().strip()

        # No payment header → return 402 challenge
        if not payment_header:
            host = _host(scope)
            challenge = build_payment_challenge(symbols, host)
            challenge_json = json.dumps(challenge).encode()
            await send({
                "type": "http.response.start",
                "status": 402,
                "headers": [
                    (b"x-payment-required", challenge_json),
                    (b"content-type", b"application/json"),
                ],
            })
            body = json.dumps({
                "error": "Payment Required",
                "description": "Retry this request with a valid X-Payment header.",
                "paymentRequired": challenge,
            }).encode()
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        # Verify payment (fixed code — never LLM)
        ok, err = verify_payment_proof(payment_header)
        if not ok:
            logger.warning("x402 payment rejected for %s: %s", symbols, err)
            await _send_json(send, 402, {
                "error": "Payment verification failed",
                "detail": err,
            })
            return

        logger.info("x402 payment verified — streaming analysis for %s", symbols)
        analysis_type = str(req.get("analysis_type") or "comprehensive")
        await self._stream_sse(send, symbols, analysis_type)

    async def _stream_sse(self, send, symbols: list[str], analysis_type: str) -> None:
        """Stream SSE events: progress → report → done."""
        from seller_core import _build_stock_analysis_prompt

        # Unique session id per delivery (avoids ADK session collision with ERC-8183 jobs)
        session_id = f"x402-{hashlib.sha256(f'{symbols}:{time.time()}'.encode()).hexdigest()[:12]}"

        # Build prompt (same pipeline as ERC-8183)
        task_json = json.dumps({
            "task": f"Analyze {', '.join(symbols)}",
            "terms": {"symbols": symbols, "analysis_type": analysis_type},
        })
        prompt, effective_symbols = _build_stock_analysis_prompt(task_json)

        # Start SSE response
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache"),
                (b"x-accel-buffering", b"no"),
                (b"transfer-encoding", b"chunked"),
            ],
        })

        async def _emit(event: str, data: dict) -> None:
            frame = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            await send({
                "type": "http.response.body",
                "body": frame.encode("utf-8"),
                "more_body": True,
            })

        try:
            await _emit("progress", {
                "stage": "starting",
                "symbols": effective_symbols or symbols,
                "message": f"Starting analysis for {', '.join(effective_symbols or symbols)}...",
            })

            async for event_name, data in self._stream_work(
                prompt, session_id, effective_symbols or symbols
            ):
                await _emit(event_name, data)

        except Exception as exc:
            logger.exception("x402 SSE delivery failed for %s", symbols)
            await _emit("error", {"message": str(exc)})

        # End stream
        await send({"type": "http.response.body", "body": b"", "more_body": False})


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_symbols(raw) -> list[str]:
    """Accept a list, a comma-string, or a single string → upper-cased list."""
    if isinstance(raw, list):
        return [str(s).strip().upper() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return []


def _host(scope) -> str:
    headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
    return (headers.get(b"host") or b"localhost:9000").decode()


async def _send_json(send, status: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})
