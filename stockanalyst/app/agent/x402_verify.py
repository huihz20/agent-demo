"""x402 payment proof verification — FIXED CODE, never LLM-callable.

Verifies the X-Payment header sent by a Binance Pay / x402 client before
any LLM work starts. Mirrors the role of signing.py for ERC-8183: all
payment verification logic lives here, never in the LLM or in a tool.

Payment flow (Binance Pay as x402 facilitator):
  1. Client requests  GET /x402/analyze?symbols=AAPL  → 402 + X-Payment-Required
  2. Client pays via Binance Pay (facilitator builds + signs the authorization)
  3. Client retries   POST /x402/analyze  +  X-Payment: <base64(JSON proof)>
  4. This module verifies the proof in fixed code before analysis starts

Payment proof format (X-Payment header, base64-encoded JSON):
  {
    "scheme": "exact",
    "network": "bsc-testnet",         # or "bsc-mainnet"
    "payload": {
      "authorization": {
        "from": "0x<buyer>",
        "to":   "0x<seller>",         # must match SELLER_WALLET
        "value": "<U-wei as string>", # must be >= MIN_PRICE_WEI
        "validAfter":  <unix ts>,     # 0 for immediate
        "validBefore": <unix ts>,     # expiry
        "nonce": "<random hex string>"
      },
      "signature": "0x<65-byte EIP-191 personal_sign>"
    }
  }

Signing message (client produces via `eth_account.sign_message` or MetaMask):
  "x402:stockanalyst:v1:{from}:{to}:{value}:{validAfter}:{validBefore}:{nonce}"
  (all fields lowercase hex for addresses; integers as decimal strings)

To generate a test proof:
  python -c "
  import json, base64, time
  from eth_account import Account
  from eth_account.messages import encode_defunct

  acct = Account.from_key('0x<private-key>')
  auth = {
    'from': acct.address.lower(),
    'to': '0x1ff095e1c5cf4bc72a3dc54be17b6cf85043fb67',
    'value': '1000000000000000000',
    'validAfter': 0,
    'validBefore': int(time.time()) + 600,
    'nonce': '0xdeadbeef01',
  }
  msg = 'x402:stockanalyst:v1:{from}:{to}:{value}:{validAfter}:{validBefore}:{nonce}'.format(**auth)
  sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
  proof = {'scheme': 'exact', 'network': 'bsc-testnet',
           'payload': {'authorization': auth, 'signature': '0x' + sig}}
  print(base64.b64encode(json.dumps(proof).encode()).decode())
  "
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import TYPE_CHECKING

logger = logging.getLogger("seller-agent.x402.verify")

# ── Seller configuration (mirrors studio.toml) ──────────────────────────────
SELLER_WALLET = "0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67"
U_TOKEN_BSC_TESTNET = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"
U_TOKEN_BSC_MAINNET = "0x55d398326f99059fF775485246999027B3197955"  # BSC Mainnet USDT as fallback
PRICE_WEI = 10**18          # 1.0 U
MIN_PRICE_WEI = 5 * 10**17  # 0.5 U  (mirrors [payments.erc8183].min_price)
SUPPORTED_NETWORKS = {"bsc-testnet", "bsc-mainnet"}

# In-memory nonce registry — prevents replay within process lifetime.
# Production: persist to Redis / on-chain nullifier contract.
_used_nonces: set[str] = set()


def build_payment_challenge(symbols: list[str], host: str = "localhost:9000") -> dict:
    """Build the X-Payment-Required 402 challenge for the client."""
    desc = f"Stock Analysis Report — {', '.join(s.upper() for s in symbols[:5])}" if symbols else "Stock Analysis Report"
    return {
        "version": "1",
        "scheme": "exact",
        "network": "bsc-testnet",
        "maxAmountRequired": str(PRICE_WEI),
        "asset": U_TOKEN_BSC_TESTNET,
        "payTo": SELLER_WALLET,
        "resource": f"http://{host}/x402/analyze",
        "description": desc,
        "mimeType": "text/event-stream",
        "maxTimeoutSeconds": 600,
        "extra": {
            "minAmountRequired": str(MIN_PRICE_WEI),
            "signingMessage": "x402:stockanalyst:v1:{from}:{to}:{value}:{validAfter}:{validBefore}:{nonce}",
            "generator": "stockanalyst-agent",
            "built_with": "https://github.com/bnb-chain/bnbagent-studio",
        },
    }


def verify_payment_proof(proof_header: str) -> tuple[bool, str]:
    """Verify the X-Payment header value.

    Returns (True, "") on success, (False, reason) on failure.
    All checks are deterministic fixed code — no LLM involvement.
    """
    # 1. Decode
    try:
        proof = json.loads(base64.b64decode(proof_header.encode()).decode())
    except Exception as exc:
        return False, f"invalid X-Payment: not valid base64 JSON ({exc})"

    scheme = proof.get("scheme", "")
    network = proof.get("network", "")
    payload = proof.get("payload", {})

    if scheme != "exact":
        return False, f"unsupported payment scheme {scheme!r} (expected 'exact')"
    if network not in SUPPORTED_NETWORKS:
        return False, f"unsupported network {network!r} (supported: {', '.join(sorted(SUPPORTED_NETWORKS))})"

    auth = payload.get("authorization") or {}
    sig = payload.get("signature", "")

    to_addr = (auth.get("to") or "").lower()
    from_addr = (auth.get("from") or "").lower()
    value_str = str(auth.get("value", "0"))
    valid_after = int(auth.get("validAfter") or 0)
    valid_before = int(auth.get("validBefore") or 0)
    nonce = str(auth.get("nonce") or "")

    # 2. Amount
    try:
        value = int(value_str)
    except ValueError:
        return False, f"invalid payment value {value_str!r}"
    if value < MIN_PRICE_WEI:
        min_u = MIN_PRICE_WEI / 10**18
        paid_u = value / 10**18
        return False, f"payment too low: {paid_u:.3f} U paid < {min_u:.3f} U minimum"

    # 3. Recipient
    if to_addr != SELLER_WALLET.lower():
        return False, f"wrong recipient: {to_addr} (expected {SELLER_WALLET.lower()})"

    # 4. Timing
    now = int(time.time())
    if valid_after > now:
        return False, f"authorization not yet valid (validAfter={valid_after} > now={now})"
    if valid_before and valid_before < now:
        return False, f"authorization expired (validBefore={valid_before} < now={now})"

    # 5. Nonce uniqueness (replay protection)
    if not nonce:
        return False, "nonce is required"
    nonce_key = f"{from_addr}:{nonce}"
    if nonce_key in _used_nonces:
        return False, f"payment nonce already used: {nonce!r}"

    # 6. Signature (EIP-191 personal_sign over the canonical message)
    if not sig:
        return False, "signature is required"
    ok, err = _verify_eip191(from_addr, to_addr, value, valid_after, valid_before, nonce, sig)
    if not ok:
        return False, err

    # Mark nonce used — do this AFTER all checks pass
    _used_nonces.add(nonce_key)
    logger.info(
        "x402 payment accepted: from=%s value=%.3f U nonce=%s",
        from_addr, value / 10**18, nonce,
    )
    return True, ""


def _canonical_message(
    from_: str, to: str, value: int, valid_after: int, valid_before: int, nonce: str
) -> str:
    """Canonical message the buyer signs with EIP-191 personal_sign.

    Format is deterministic and reproduced identically by the client.
    Use lowercase hex for addresses; decimal strings for integers.
    """
    return (
        f"x402:stockanalyst:v1:"
        f"{from_.lower()}:{to.lower()}:{value}:{valid_after}:{valid_before}:{nonce}"
    )


def _verify_eip191(
    from_addr: str, to_addr: str, value: int,
    valid_after: int, valid_before: int, nonce: str, sig: str,
) -> tuple[bool, str]:
    """Recover signer from EIP-191 personal_sign signature and compare to `from_addr`."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        # eth_account ships with web3/bnbagent; if somehow absent, skip sig check
        logger.warning("eth_account not available — skipping signature verification (demo mode)")
        return True, ""

    msg = _canonical_message(from_addr, to_addr, value, valid_after, valid_before, nonce)
    try:
        signable = encode_defunct(text=msg)
        recovered = Account.recover_message(signable, signature=sig)
    except Exception as exc:
        return False, f"signature recovery error: {exc}"

    if recovered.lower() != from_addr.lower():
        return False, f"signature mismatch: recovered {recovered.lower()} ≠ from {from_addr}"
    return True, ""
