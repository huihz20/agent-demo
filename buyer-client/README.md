# ERC-8183 Buyer Client (TypeScript)

TypeScript buyer client that integrates **UOMP user context** with the **ERC-8183 commerce protocol** to buy stock analysis from a cloud-deployed seller agent. Report delivery uses the **UOMP reverse gateway** — a Cloudflare Tunnel lets the seller push the deliverable directly to the buyer's local machine without requiring a public IP.

## Architecture

```
┌─────────────────── LOCAL (buyer machine) ───────────────────────┐
│                                                                   │
│  UOMP Guard mock                                                  │
│  (localhost:9374)                                                 │
│   portfolio:holdings  ──► buildTaskFromMemory()                   │
│   profile:risk                    │                               │
│                                   ▼                               │
│                          buyer-client (Node.js)                   │
│                                   │                               │
│                  ┌────────────────┼─────────────────┐            │
│                  │                │                  │            │
│                  ▼                ▼                  ▼            │
│          A2A negotiate    UOMP Gateway relay   BSC Testnet        │
│          (OAuth2 token)   localhost:9444       on-chain ops       │
│                  │                │                               │
│                  │         Cloudflare Tunnel                      │
│                  │         https://xxx.trycloudflare.com          │
│                  │                │                               │
└──────────────────┼────────────────┼───────────────────────────────┘
                   │                │
                   │  (public internet)
                   │                │
┌──────────────────▼────────────────▼───────────────────────────────┐
│              BNB Chain Platform (cloud seller)                     │
│                                                                    │
│   seller agent (AgentCore runtime)                                 │
│    ├─ negotiate skill  ◄── A2A JSON-RPC ──────────────────────────┤
│    └─ notify_funded skill                                          │
│         └─ runs LLM stock analysis                                 │
│         └─ POST report ──► Cloudflare Tunnel ──► buyer relay       │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

BSC Testnet contracts (chain 97):
  AgenticCommerce  0xa206...   EvaluatorRouter  0xd7d3...
  OptimisticPolicy 0x4f46...   U Token          0xc70B...
```

## Test flow (7 steps)

| Step | What happens |
|---|---|
| 1 | Load UOMP user context — portfolio (AAPL, NVDA) + risk profile from Guard |
| 2 | A2A negotiate — fetch OAuth2 token, send signed quote request to cloud seller |
| 3 | On-chain — `createJob → registerJob → setBudget → approve → fund` (5 txs) |
| 4 | `notify_funded` — tell seller job is funded, pass Cloudflare Tunnel URL + token |
| 5 | Poll on-chain until job status reaches `SUBMITTED` |
| 6 | Fetch deliverable via tunnel URL → display report |
| 7 | Settle — release escrow to seller (available after 24h dispute window) |

## Setup

```bash
# Install cloudflared (required for remote seller)
brew install cloudflare/cloudflare/cloudflared

# Install dependencies
npm install

# Configure environment
cp .env.example .env
```

`.env` variables:

| Variable | Description |
|---|---|
| `KEYSTORE_PATH` | Path to buyer's encrypted keystore JSON |
| `WALLET_PASSWORD` | Keystore decryption password |
| `AGENT_ENDPOINT` | Cloud seller A2A URL (from platform deploy output) |
| `AGENT_CLIENT_ID` | OAuth2 client ID for the platform agent |
| `AGENT_CLIENT_SECRET` | OAuth2 client secret for the platform agent |
| `PROVIDER_ADDRESS` | Seller wallet address (for on-chain job registration) |
| `UOMP_GUARD_URL` | UOMP Memory Guard base URL (default: `http://127.0.0.1:9374`) |
| `UOMP_GUARD_TOKEN` | Bearer token accepted by the Guard |

## Run

**Terminal 1 — start the UOMP Guard mock:**

```bash
cd ..  # agent-demo root
node guard-mock.mjs
```

**Terminal 2 — run the buyer client:**

```bash
npm run dev
```

The client prints step-by-step progress and displays the delivered report inline. After the 24-hour dispute window, settle the escrow:

```bash
cd ../stockanalyst/app/agent
bag erc8183 settle <job_id>
```

## BSC Testnet resources

| Resource | Link |
|---|---|
| tBNB faucet (gas) | https://testnet.bnbchain.org/faucet-smart |
| U token faucet (escrow) | https://united-coin-u.github.io/u-faucet/ |
| BSC testnet explorer | https://testnet.bscscan.com |
