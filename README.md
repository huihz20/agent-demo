# Agent Demo — ERC-8183 + UOMP

[![ERC-8183](https://img.shields.io/badge/Protocol-ERC--8183-blue)](https://github.com/bnb-chain/BEPs)
[![UOMP](https://img.shields.io/badge/Context-UOMP-purple)](https://github.com/0xaicrypto/uomp-core)
[![Network](https://img.shields.io/badge/Network-BSC%20Testnet-yellow)](https://testnet.bscscan.com)
[![Python](https://img.shields.io/badge/Seller-Python%203.12-3776AB?logo=python)](stockanalyst/)
[![TypeScript](https://img.shields.io/badge/Buyer-TypeScript-3178C6?logo=typescript)](buyer-client/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green)](LICENSE)

End-to-end demo of a **personalized AI stock analyst** bought and paid for on BNB Chain.

The agent aggregates 5 independent data sources (yfinance, FRED macro, SEC EDGAR insider trades, Alpha Vantage AI sentiment, GNews headlines), computes 10 technical indicators (RSI, MACD, Bollinger, MA50/200 golden/death cross, ADX trend strength, OBV, ATR, VaR 95%), and writes a structured report with explicit bull/bear thesis, portfolio P&L vs your actual cost basis, and a hard recommendation with target price.

Payment is trustless: the buyer's 1.0 U is locked in a smart contract escrow and only released after the agent submits a verifiable deliverable on-chain.

- **Seller** (`stockanalyst/`) — stock analysis agent deployed on BNB Chain platform: two-stage LLM (data collection → report writing), 5-source data pipeline, portfolio-personalized output
- **Buyer** (`buyer-client/`) — TypeScript client that reads the user's portfolio and cost basis from a local UOMP Guard, pays via ERC-8183 escrow, and receives the report through a Cloudflare Tunnel reverse gateway

## Architecture

```
  LOCAL (buyer machine)                      CLOUD / CHAIN
  ──────────────────────                     ─────────────

  UOMP Guard (localhost:9374)
  ├─ portfolio: AAPL ×50, NVDA ×20
  └─ profile:  moderate / 12mo
        │
        │ [1] read context
        ▼
  buyer-client (Node.js)
        │
        ├─[2]─ A2A negotiate ─────────────► seller agent (BNB Chain Platform)
        │      OAuth2 token                  └─ sign quote → 1.0 U
        │◄─────────────────────────────────── signed quote
        │
        ├─[3]─ createJob ─────────────────► BSC Testnet (chain 97)
        │      registerJob                   AgenticCommerce
        │      setBudget                     U token locked in escrow
        │      approve + fund
        │
        ├─ start relay (localhost:9444)
        │  Cloudflare Tunnel ──────────────► https://xxx.trycloudflare.com
        │                                          │
        ├─[4]─ notify_funded ────────────► seller agent
        │      + tunnel URL + token         ├─ LLM analysis (Kimi)
        │                                   ├─[5]─ submit_result ─► BSC Testnet
        │                                   └─[6]─ POST report ───► Cloudflare Tunnel
        │                                                                 │
        │◄────────────────────────────────────────────────────────────────┘
        │      report delivered to local relay
        │
        ├─[5]─ poll getJob() ─────────────► BSC Testnet → SUBMITTED
        │
        ├─[6]─ fetch report via tunnel URL
        │      displayed inline
        │
        └─[7]─ settle (after 24h) ────────► BSC Testnet
                                             escrow released to seller
```

## E2E Test Flow

| Step | Who | Action |
|------|-----|--------|
| 1 | Buyer | Read UOMP Guard → AAPL/NVDA holdings + risk profile |
| 2 | Buyer→Seller | A2A negotiate (OAuth2) → signed quote 1.0 U |
| 3 | Buyer→Chain | createJob → registerJob → setBudget → approve → fund |
| 4 | Buyer→Seller | notify_funded with Cloudflare Tunnel URL + token |
| 5 | Seller | LLM analysis → submit_result → POST report to tunnel |
| 6 | Buyer | Poll chain → SUBMITTED → fetch report from local relay |
| 7 | Buyer→Chain | settle (after 24h dispute window) |

## Setup

### 1. Deploy the seller

```bash
cd stockanalyst/app/agent

# Single-quote required — bash expands $x in unquoted values,
# silently stripping characters from passwords that contain them
export WALLET_PASSWORD='<your-keystore-password>'

bag deploy agent
```

Record `agent_id` from `studio.toml [deploy.platform]` and create an OAuth2 client in the platform console.

### 2. Configure the buyer

```bash
brew install cloudflare/cloudflare/cloudflared  # macOS; required for remote seller

cd buyer-client
npm install
cp .env.example .env
```

Edit `buyer-client/.env`:

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/<address>.json
WALLET_PASSWORD=<your-keystore-password>
AGENT_ENDPOINT=https://bnbagent-api.bnbchain.world/v1/rt/<agent_id>/a2a
AGENT_CLIENT_ID=<client_id>
AGENT_CLIENT_SECRET=<client_secret>
PROVIDER_ADDRESS=<seller wallet address>
UOMP_GUARD_URL=http://127.0.0.1:9374
UOMP_GUARD_TOKEN=demo-guard-token
```

Buyer wallet needs: ≥ 0.01 tBNB (gas) + ≥ 1.0 U (escrow).

### 3. Run

**Terminal 1 — UOMP Guard mock:**

```bash
node guard-mock.mjs
```

**Terminal 2 — buyer client:**

```bash
cd buyer-client && npm run dev
```

The client prints step-by-step progress and displays the delivered report inline.

After the 24-hour dispute window:

```bash
cd stockanalyst/app/agent && bag erc8183 settle <job_id>
```

## Repository Structure

```
agent-demo/
├── guard-mock.mjs          UOMP Guard mock (portfolio + risk profile)
├── stockanalyst/           Seller agent
│   ├── app/agent/          Python source (main.py, signing.py, seller_core.py, …)
│   └── README.md           Seller architecture + deploy guide
└── buyer-client/           TypeScript buyer client
    ├── src/
    │   ├── index.ts        Main E2E flow (Steps 1–7)
    │   ├── uomp.ts         UOMP memory layer (GuardUserMemory)
    │   ├── gateway.ts      Cloudflare Tunnel relay
    │   ├── negotiate.ts    A2A negotiate + notify_funded (OAuth2)
    │   └── erc8183.ts      On-chain buyer ops
    └── README.md           Buyer setup + env vars
```

## BSC Testnet Contracts (chain 97)

| Contract | Address |
|----------|---------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

| Resource | Link |
|----------|------|
| tBNB faucet (gas) | https://testnet.bnbchain.org/faucet-smart |
| U token faucet | https://united-coin-u.github.io/u-faucet/ |
| BSC testnet explorer | https://testnet.bscscan.com |
