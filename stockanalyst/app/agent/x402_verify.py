"""x402 v2 payment proof verification — FIXED CODE, never LLM-callable.

Signing scheme: EIP-712 TransferWithAuthorization (EIP-3009).
The buyer uses their Web3 wallet (Binance Web3 Wallet / MetaMask / ethers.js
signTypedData) to sign a typed-data authorization; the seller verifies the
EIP-712 signature locally in this module. On-chain settlement is handled
separately by the Binance Pay x402 facilitator (called from x402_handler.py).

Wire format — X-Payment header = base64(JSON):
  {
    "x402Version": 2,
    "scheme":      "exact",
    "network":     "eip155:97",           // BSC Testnet (eip155:<chainId>)
    "payload": {
      "signature":     "0x<65-byte EIP-712 sig>",
      "authorization": {
        "from":        "0x<buyer>",
        "to":          "0x<seller>",       // must equal SELLER_WALLET
        "value":       "1000000000000000000",  // 1.0 U in wei (≥ MIN_PRICE_WEI)
        "validAfter":  "0",
        "validBefore": "<unix_ts>",        // +10 min TTL recommended
        "nonce":       "0x<32 random bytes>"  // bytes32
      }
    }
  }

EIP-712 domain for U token (BSC Testnet):
  name:              env U_TOKEN_DOMAIN_NAME    (default "U")
  version:           env U_TOKEN_DOMAIN_VERSION (default "1")
  chainId:           97
  verifyingContract: 0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565

EIP-712 primary type: TransferWithAuthorization(
    address from, address to, uint256 value,
    uint256 validAfter, uint256 validBefore, bytes32 nonce)

To generate a test proof (Python):
  python - <<'EOF'
  import json, base64, time, os
  from eth_account import Account
  from eth_utils import keccak, to_checksum_address
  import eth_abi, secrets

  PRIV = "0x<private-key>"
  SELLER = "0x1ff095e1c5cf4bc72a3dc54be17b6cf85043fb67"
  TOKEN  = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"
  acct = Account.from_key(PRIV)

  domain_type = keccak(text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
  domain_sep  = keccak(eth_abi.encode(["bytes32","bytes32","bytes32","uint256","address"],
    [domain_type, keccak(text="U"), keccak(text="1"), 97, to_checksum_address(TOKEN)]))
  type_hash = keccak(text="TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)")
  nonce = secrets.token_bytes(32)
  auth = {"from": acct.address.lower(), "to": SELLER,
          "value": "1000000000000000000", "validAfter": "0",
          "validBefore": str(int(time.time())+600), "nonce": "0x"+nonce.hex()}
  struct_hash = keccak(eth_abi.encode(
    ["bytes32","address","address","uint256","uint256","uint256","bytes32"],
    [type_hash, to_checksum_address(auth["from"]), to_checksum_address(auth["to"]),
     int(auth["value"]), int(auth["validAfter"]), int(auth["validBefore"]), nonce]))
  digest = keccak(b"\\x19\\x01" + domain_sep + struct_hash)
  sig = Account._sign_hash(digest, PRIV).signature.hex()
  proof = {"x402Version":2,"scheme":"exact","network":"eip155:97",
           "payload":{"signature":"0x"+sig,"authorization":auth}}
  print(base64.b64encode(json.dumps(proof).encode()).decode())
  EOF
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time

from eth_account import Account

_log = logging.getLogger("seller-agent.x402.verify")

SELLER_WALLET       = "0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67"
U_TOKEN_BSC_TESTNET = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"
PRICE_WEI           = 10**18         # 1.0 U
MIN_PRICE_WEI       = 5 * 10**17    # 0.5 U
CHAIN_ID            = 97            # BSC Testnet

# U token EIP-712 domain — set env vars if the deployed contract differs.
# Verify via: cast call <U_TOKEN> "name()" --rpc-url $BSC_TESTNET_RPC
_TOKEN_DOMAIN_NAME    = os.environ.get("U_TOKEN_DOMAIN_NAME",    "U")
_TOKEN_DOMAIN_VERSION = os.environ.get("U_TOKEN_DOMAIN_VERSION", "1")

# Replay protection: in-memory nonce registry keyed by "from:nonce".
# Production deployments should persist this (Redis / on-chain nullifier).
_used_nonces: set[str] = set()


# ── EIP-712 hashing ────────────────────────────────────────────────────────────

def _keccak(data: bytes) -> bytes:
    from eth_utils import keccak as _k
    return _k(data)


def _ktext(text: str) -> bytes:
    from eth_utils import keccak as _k
    return _k(text=text)


_DOMAIN_TYPE_HASH = _ktext(
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
_TRANSFER_TYPE_HASH = _ktext(
    "TransferWithAuthorization(address from,address to,uint256 value,"
    "uint256 validAfter,uint256 validBefore,bytes32 nonce)"
)


def _domain_separator() -> bytes:
    import eth_abi
    from eth_utils import to_checksum_address
    return _keccak(eth_abi.encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [
            _DOMAIN_TYPE_HASH,
            _ktext(_TOKEN_DOMAIN_NAME),
            _ktext(_TOKEN_DOMAIN_VERSION),
            CHAIN_ID,
            to_checksum_address(U_TOKEN_BSC_TESTNET),
        ],
    ))


def _eip712_digest(
    from_: str, to: str, value: int,
    valid_after: int, valid_before: int, nonce: bytes,
) -> bytes:
    """keccak256(\\x19\\x01 || domain_separator || struct_hash)."""
    import eth_abi
    from eth_utils import to_checksum_address
    struct_hash = _keccak(eth_abi.encode(
        ["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
        [
            _TRANSFER_TYPE_HASH,
            to_checksum_address(from_),
            to_checksum_address(to),
            value,
            valid_after,
            valid_before,
            nonce,
        ],
    ))
    return _keccak(b"\x19\x01" + _domain_separator() + struct_hash)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_payment_challenge(symbols: list[str], host: str = "localhost:9000") -> dict:
    """Return x402 v2 standard payment challenge (HTTP 402 body / X-Payment-Required header)."""
    return {
        "x402Version": 2,
        "accepts": [
            {
                "scheme":            "exact",
                "network":           f"eip155:{CHAIN_ID}",
                "maxAmountRequired": str(PRICE_WEI),
                "asset":             U_TOKEN_BSC_TESTNET,
                "payTo":             SELLER_WALLET.lower(),
                "maxTimeoutSeconds": 600,
                "extra": {
                    "assetTransferMethod": "eip3009",
                    "name":    _TOKEN_DOMAIN_NAME,
                    "version": _TOKEN_DOMAIN_VERSION,
                    "description": (
                        f"Stock analysis for {', '.join(s.upper() for s in symbols)}"
                        if symbols else "Stock analysis report"
                    ),
                },
            }
        ],
        "error":    "Payment Required",
        "resource": f"http://{host}/x402/analyze",
    }


def verify_payment_proof(proof_header: str) -> tuple[bool, str]:
    """Verify an x402 v2 EIP-712 payment proof (local signature check only).

    Does NOT call the facilitator — that is done asynchronously by the handler.
    Returns (True, "") on success, (False, human-readable reason) on failure.
    """
    # ── Decode ─────────────────────────────────────────────────────────────────
    try:
        raw   = base64.b64decode(proof_header.strip())
        proof = json.loads(raw)
    except Exception:
        return False, "X-Payment is not valid base64 JSON"

    # ── x402 version / scheme / network ───────────────────────────────────────
    if proof.get("x402Version") != 2:
        return False, f"unsupported x402Version: {proof.get('x402Version')!r} (expected 2)"
    if proof.get("scheme", "exact") != "exact":
        return False, f"unsupported scheme: {proof.get('scheme')!r}"
    network = proof.get("network", f"eip155:{CHAIN_ID}")
    if network != f"eip155:{CHAIN_ID}":
        return False, f"wrong network: {network!r} (expected eip155:{CHAIN_ID})"

    payload = proof.get("payload") or {}
    auth    = payload.get("authorization") or {}
    sig     = str(payload.get("signature") or "")

    # ── Authorization field validation ─────────────────────────────────────────
    from_addr    = str(auth.get("from",        "")).lower()
    to_addr      = str(auth.get("to",          "")).lower()
    value_raw    = str(auth.get("value",       "0"))
    valid_after  = int(auth.get("validAfter",  0))
    valid_before = int(auth.get("validBefore", 0))
    nonce_hex    = str(auth.get("nonce", "0x" + "00" * 32))

    if not from_addr.startswith("0x") or len(from_addr) != 42:
        return False, f"invalid from address: {from_addr!r}"
    if to_addr != SELLER_WALLET.lower():
        return False, f"wrong recipient: {to_addr!r} (expected {SELLER_WALLET.lower()!r})"
    try:
        value = int(value_raw)
    except (ValueError, TypeError):
        return False, f"invalid value: {value_raw!r}"
    if value < MIN_PRICE_WEI:
        return False, f"value {value / 1e18:.3f} U < minimum {MIN_PRICE_WEI / 1e18:.3f} U"

    now = int(time.time())
    if now < valid_after:
        return False, "authorization not yet valid"
    if now > valid_before:
        return False, "authorization expired"
    if valid_before - valid_after > 3600:
        return False, "authorization TTL exceeds 1 hour"
    if not sig.startswith("0x"):
        return False, "signature must be a 0x-prefixed hex string"

    # ── Replay protection ──────────────────────────────────────────────────────
    nonce_key = f"{from_addr}:{nonce_hex}"
    if nonce_key in _used_nonces:
        return False, "nonce already used (replay blocked)"

    # ── EIP-712 signature verification ─────────────────────────────────────────
    try:
        nonce_bytes = bytes.fromhex(nonce_hex.removeprefix("0x").zfill(64))
        digest      = _eip712_digest(
            from_addr, to_addr, value, valid_after, valid_before, nonce_bytes,
        )
        recovered = Account._recover_hash(digest, signature=sig)
    except Exception as exc:
        return False, f"EIP-712 verification error: {exc}"

    if recovered.lower() != from_addr:
        return False, (
            f"signature mismatch: recovered {recovered.lower()!r} ≠ from {from_addr!r}"
        )

    # ── Accept — mark nonce used ────────────────────────────────────────────────
    _used_nonces.add(nonce_key)
    _log.info(
        "x402 payment accepted: from=%s value=%.3f U nonce=%s",
        from_addr, value / 1e18, nonce_hex,
    )
    return True, ""
