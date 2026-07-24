# Stock Analysis Agent — Buyer Client

TypeScript buyer client for the [Stock Analysis Agent](../stockanalyst/README.md). Supports two payment channels — pick the one that fits your use case:

| Channel | Command | Friction | Trust model |
|---------|---------|----------|-------------|
| **x402** (Binance Pay) | `npm run x402` | Low — 1 signature, 1 HTTP call | Off-chain |
| **ERC-8183** (on-chain escrow) | `npm run dev` | Higher — 5 on-chain txs + polling | Trustless, dispute window |

Both channels read the buyer's portfolio from a local **UOMP Memory Guard** and produce the same HTML + PDF report.

---

## Architecture

```
┌─────────────── LOCAL (buyer machine) ────────────────────────────┐
│                                                                    │
│  UOMP Memory Guard  (localhost:9374)                               │
│  ├─ portfolio:holdings  (AAPL 50sh @ $178, NVDA 20sh @ $412 …)    │
│  └─ profile:risk        (moderate / 12mo)                          │
│            │                                                       │
│            ▼                                                       │
│     buyer-client (Node.js)                                         │
│            │                                                       │
│    ┌───────┴──────────┐                                            │
│    │                  │                                            │
│  x402 flow      ERC-8183 flow                                      │
│    │                  │                                            │
│ 1 POST /x402/analyze  │  A2A negotiate → createJob → fund (5 txs)  │
│   X-Payment: <proof>  │  notify_funded → poll chain → settle        │
│    │                  │                                            │
│    └──── SSE stream ──┘                                            │
│                  │                                                 │
│           saveReport()                                             │
│           stock-analysis-<id>.html  +  .pdf                        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                          │
               ┌──────────┴──────────┐
               │ Local (bag dev)     │  Cloud (BNB Chain Platform)
               │ localhost:9000      │  bnbagent-api.bnbchain.world
               │ x402 + ERC-8183     │  ERC-8183 only (A2A + Cognito)
               └─────────────────────┘
```

---

## Prerequisites

- **Node.js 18+** (for native `fetch` + `ReadableStream`)
- **tBNB** in your wallet (gas) — [BSC Testnet Faucet](https://testnet.bnbchain.org/faucet-smart)
- **U token** in your wallet (payment) — [U Faucet](https://united-coin-u.github.io/u-faucet/)
- **UOMP Guard** running locally on port 9374
- **Agent** running locally on port 9000 (for x402) or deployed on platform (for ERC-8183)

---

## Setup

```bash
cd buyer-client
npm install
cp .env.example .env
# Edit .env — see variable reference below
```

### Environment variables

```bash
# .env

# ── Wallet ────────────────────────────────────────────────────────
KEYSTORE_PATH=../stockanalyst/.studio/wallets/<address>.json
WALLET_PASSWORD=your_wallet_password

# ── ERC-8183 (cloud seller, requires OAuth2) ──────────────────────
AGENT_ENDPOINT=https://bnbagent-api.bnbchain.world/v1/rt/<runtime-id>/a2a
AGENT_CLIENT_ID=<cognito-client-id>        # from `bag deploy provision-cognito`
AGENT_CLIENT_SECRET=<cognito-secret>
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67

# ── x402 (local agent, no auth needed) ───────────────────────────
X402_ENDPOINT=http://localhost:9000        # default — set only to override

# ── UOMP Memory Guard ─────────────────────────────────────────────
UOMP_GUARD_URL=http://127.0.0.1:9374
UOMP_GUARD_TOKEN=your_guard_jwt_token
```

> **Note:** `X402_ENDPOINT` and `AGENT_ENDPOINT` are separate.
> `AGENT_ENDPOINT` is the deployed A2A path (requires Cognito auth) used by `npm run dev`.
> `X402_ENDPOINT` is the bare local agent URL used by `npm run x402` — it defaults to
> `http://localhost:9000` so you only need to add it to `.env` if you change the port.

---

## Quick start — x402 (recommended for local testing)

x402 is the fastest path: one EIP-191 signature, one HTTP POST, SSE stream back. No gas, no polling.

**Terminal 1 — start the agent locally:**
```bash
cd ../stockanalyst
bag dev --agent-only         # starts agent on localhost:9000
```

**Terminal 2 — seed the UOMP Guard, then run the buyer:**
```bash
cd ..                        # agent-demo root
node guard-mock.mjs          # seed portfolio + risk profile into Guard

cd buyer-client
npm run x402
```

Expected output:
```
Decrypting keystore...

────────────────────────────────────────────────────────────
  Step 1: Load UOMP user context (portfolio + risk profile)
────────────────────────────────────────────────────────────
  ✓ Symbols:  AAPL, NVDA
  ✓ Holdings: 2 positions
  ✓ Risk:     moderate / 12mo

════════════════════════════════════════════════════════════
  Stock Analysis Agent — x402 Buyer
  x402 endpoint: http://localhost:9000
  Buyer:         0x8fD3…4a2B
  Symbols:       AAPL, NVDA
  Payment:       x402 / EIP-191 signed (no on-chain tx)
  Delivery:      SSE stream (no polling needed)
════════════════════════════════════════════════════════════

────────────────────────────────────────────────────────────
  Step 2: Sign x402 payment authorization (EIP-191, no on-chain tx)
────────────────────────────────────────────────────────────
  ✓ Payment proof signed
  ✓ Paying:   1.0 U → 0x1ff0…fb67
  ✓ Valid for: 10 minutes

────────────────────────────────────────────────────────────
  Step 3: POST /x402/analyze → streaming SSE report
────────────────────────────────────────────────────────────
  (progress events below — full analysis takes 40–120s)

  →  Starting analysis for AAPL, NVDA...
  ⟳  get_stock_quote
  ⟳  get_technical_signals
  ⟳  get_options_sentiment
  ⟳  get_insider_activity
  ⟳  get_news_sentiment
  ⟳  get_stock_quote          ← second symbol
  ⟳  get_technical_signals
  …
  ✎  Rendering report...
  ✓ Report received (21 840 chars)

────────────────────────────────────────────────────────────
  Step 4: Save report as HTML + PDF
────────────────────────────────────────────────────────────
  ✓ HTML  stock-analysis-x402-xxxxxx.html
  ✓ PDF   stock-analysis-x402-xxxxxx.pdf
```

---

## ERC-8183 flow (cloud seller, on-chain escrow)

The full trustless flow against the deployed agent on BNB Chain Platform.

**Requires:**
- `AGENT_ENDPOINT`, `AGENT_CLIENT_ID`, `AGENT_CLIENT_SECRET` in `.env`
- `cloudflared` installed (used for the UOMP report relay)

```bash
brew install cloudflare/cloudflare/cloudflared   # first time only
```

**Terminal 1 — UOMP Guard + relay:**
```bash
node guard-mock.mjs
```

**Terminal 2 — ERC-8183 buyer:**
```bash
cd buyer-client
npm run dev
```

The 7-step flow:

| Step | What happens | Approx. time |
|------|-------------|------|
| 1 | Load UOMP portfolio context from Guard | instant |
| 2 | A2A negotiate — OAuth2 token + signed price quote | ~2s |
| 3 | `createJob → registerJob → setBudget → approve → fund` (5 txs) | ~30–60s |
| 4 | `notify_funded` — seller starts LLM analysis | instant |
| 5 | Poll chain until `SUBMITTED` | 40–120s |
| 6 | Fetch report via UOMP tunnel | instant |
| 7 | `settle` (or run manually after 24h dispute window) | ~10s |

After the 24-hour dispute window, settle manually:
```bash
cd ../stockanalyst
bag erc8183 settle <job_id>
```

---

## Source files

```
src/
├── index.ts        — ERC-8183 buyer entry point (npm run dev)
├── x402.ts         — x402 buyer entry point     (npm run x402)
├── erc8183.ts      — on-chain job lifecycle: createJob → fund → settle
├── negotiate.ts    — A2A JSON-RPC negotiate with OAuth2 support
├── uomp.ts         — UOMP Guard HTTP client + buildTaskFromMemory()
├── gateway.ts      — Cloudflare Tunnel relay (UOMP delivery for ERC-8183)
├── pdf-report.ts   — HTML + PDF report generation via Puppeteer
└── abi/            — Solidity ABIs: Commerce, Router, Policy, ERC-20
```

---

## Payment channels explained

### x402 — Binance Pay facilitator

```
Buyer                              Agent (localhost:9000)
  │                                        │
  │  POST /x402/analyze                    │
  │  Content-Type: application/json        │
  │  X-Payment: base64(JSON proof)  ──────▶│
  │                                        │  verify_payment_proof()  ← fixed code
  │                                        │  (sig + amount + recipient + nonce)
  │◀── event: progress ────────────────────│  per tool-call progress
  │◀── event: progress ────────────────────│
  │◀── event: report   ────────────────────│  full markdown report
  │◀── event: done     ────────────────────│
```

The `X-Payment` header carries a base64-encoded JSON proof. The `x402.ts` client builds and signs it automatically:

```typescript
// Signing message (EIP-191 personal_sign via ethers.js signMessage):
const msg =
  `x402:stockanalyst:v1:${auth.from}:${auth.to}:${auth.value}` +
  `:${auth.validAfter}:${auth.validBefore}:${auth.nonce}`;
const sig = await wallet.signMessage(msg);

// Proof structure:
const proof = {
  scheme: "exact",
  network: "bsc-testnet",
  payload: {
    authorization: {
      from:        "0x<buyer-address>",
      to:          "0x1ff095e1c5cf4bc72a3dc54be17b6cf85043fb67",  // seller
      value:       "1000000000000000000",   // 1.0 U in wei (min 0.5 U)
      validAfter:  0,
      validBefore: <unix-timestamp>,        // 10-minute TTL
      nonce:       "0x<random-8-bytes>",    // replay protection
    },
    signature: "0x<65-byte EIP-191 sig>",
  },
};
// Header value: Buffer.from(JSON.stringify(proof)).toString("base64")
```

The agent checks: signature valid, `to` == seller wallet, `value` ≥ 0.5 U, not expired, nonce not reused. Each nonce can only be used once per agent process (in-memory replay protection).

**To call from any HTTP client (curl example):**
```bash
# 1. Get payment challenge
curl "http://localhost:9000/x402/analyze?symbols=AAPL"

# 2. Generate proof with the script in x402_verify.py docstring, then:
curl -N -X POST http://localhost:9000/x402/analyze \
  -H "Content-Type: application/json" \
  -H "X-Payment: <base64-proof>" \
  -d '{"symbols": ["AAPL", "NVDA"]}'
```

### ERC-8183 — on-chain trustless escrow

```
Buyer                      BSC Testnet contracts         Agent (cloud)
  │                               │                           │
  ├── createJob ─────────────────▶│                           │
  ├── registerJob ───────────────▶│                           │
  ├── setBudget  ────────────────▶│                           │
  ├── approve (U token) ─────────▶│                           │
  ├── fund (lock 1 U in escrow) ─▶│                           │
  │                               │                           │
  ├── notify_funded ──────────────┼──────────────────────────▶│
  │                               │      LLM analysis (40–120s)│
  │                               │◀─── submit_result ─────────┤
  │◀── poll SUBMITTED ────────────┤                           │
  │                               │                           │
  ├── settle ─────────────────────▶│  (releases U to seller)   │
```

---

## Building your own buyer client

To call the agent from your own code, here are the minimal integration points:

### x402 (simplest — any language/framework)

1. **GET** `http://<agent>:9000/x402/price` → read `payTo`, `maxAmountRequired`, `network`
2. Sign the canonical message with the buyer's wallet (EIP-191 personal_sign)
3. **POST** `http://<agent>:9000/x402/analyze` with `X-Payment: <base64-proof>` and `{"symbols": [...]}` body
4. Consume the `text/event-stream` response, parse `event:` / `data:` pairs

### ERC-8183 (TypeScript SDK)

Reuse the classes in `src/erc8183.ts` and `src/negotiate.ts`:

```typescript
import { ERC8183Buyer, CONTRACTS } from "./src/erc8183.js";
import { negotiate, notifyFunded } from "./src/negotiate.js";
import { GuardUserMemory, buildTaskFromMemory } from "./src/uomp.js";

const wallet  = await Wallet.fromEncryptedJson(keystoreJson, password);
const buyer   = new ERC8183Buyer(wallet);
const memory  = new GuardUserMemory();
const { symbols, task, deliverables, quality, portfolio, riskProfile } =
  await buildTaskFromMemory(memory);

const envelope = await negotiate(agentEndpoint, task, deliverables, quality);
const priceU   = Number(BigInt(envelope.response.terms.price)) / 1e18;

const buy = await buyer.buy({
  provider:    PROVIDER_ADDRESS,
  description: JSON.stringify(envelope),
  budgetU:     String(priceU),
});

await notifyFunded(agentEndpoint, buy.jobId, { portfolio, riskProfile });
await buyer.pollUntilSubmitted(buy.jobId);
const url = await buyer.getDeliverableUrl(buy.jobId, buy.fundTxBlock);
```

---

## BSC Testnet contract addresses

| Contract | Address |
|---------|---------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

Chain ID: **97** (BSC Testnet)

---

## UOMP — user-owned portfolio context

The buyer client reads portfolio context from a local **UOMP Memory Guard** at `localhost:9374`. The Guard stores data on the buyer's machine only; the agent sees context exactly as passed by the buyer on each request.

Data seeded for this demo (`guard-mock.mjs`):

```jsonc
// tag: portfolio:holdings
[
  { "symbol": "AAPL", "shares": 50, "avgCost": 178.30, "currency": "USD" },
  { "symbol": "NVDA", "shares": 20, "avgCost": 412.50, "currency": "USD" }
]

// tag: profile:risk
{
  "tolerance": "moderate",
  "horizonMonths": 12,
  "preferredIndicators": ["RSI-14", "MACD", "Bollinger Bands", "MA50/200", "ADX"]
}
```

The agent uses this to personalize the report: real P&L vs cost basis, risk-adjusted stop-loss levels, and position-specific rebalancing recommendations.

---

## Resources

| Resource | Link |
|---------|------|
| tBNB faucet (gas) | https://testnet.bnbchain.org/faucet-smart |
| U token faucet | https://united-coin-u.github.io/u-faucet/ |
| BSC Testnet Explorer | https://testnet.bscscan.com |
| Agent source | [../stockanalyst/app/agent/](../stockanalyst/app/agent/) |
| ERC-8183 spec | https://github.com/bnb-chain/BEPs |
| UOMP protocol | https://github.com/0xaicrypto/uomp-core |
