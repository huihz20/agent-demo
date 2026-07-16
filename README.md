# Stock Analysis Agent

[![ERC-8183](https://img.shields.io/badge/Protocol-ERC--8183-blue)](https://github.com/bnb-chain/BEPs)
[![UOMP](https://img.shields.io/badge/Context-UOMP-purple)](https://github.com/0xaicrypto/uomp-core)
[![Network](https://img.shields.io/badge/Network-BSC%20Testnet-yellow)](https://testnet.bscscan.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org)

> [中文](#中文) | English

---

## 1. How It Works

### Two Protocols, One Flow

This project connects two independent protocols:

| Protocol | Role | What it handles |
|----------|------|-----------------|
| **ERC-8183** | Commerce | On-chain job creation, escrow, payment settlement |
| **UOMP** | User context + delivery | Portfolio data, deliverable upload/download |

They serve different concerns and can be used independently, but together they enable a complete "personalized AI service purchase" flow where:
- The user's data stays under their control (UOMP)
- The payment is trustlessly escrowed and released (ERC-8183)
- Neither buyer nor seller needs a public IP (UOMP Gateway handles delivery)

### ERC-8183: On-Chain Commerce

ERC-8183 (AgenticCommerce) turns buying an AI service into a verifiable on-chain transaction. The buyer's payment is locked in a smart contract; the agent receives it only after submitting a verifiable deliverable URL on-chain.

```
Buyer                        Chain (BSC Testnet)              Seller Agent
  │                               │                               │
  ├── negotiate (A2A) ────────────────────────────────────────── │
  │◄─ signed quote (1.0 U) ───────────────────────────────────── │
  │                               │                               │
  ├── createJob ─────────────────►│                               │
  ├── registerJob ───────────────►│                               │
  ├── setBudget + fund ──────────►│ U token locked in escrow      │
  │                               │                               │
  ├── notify_funded (A2A) ────────────────────────────────────── │
  │◄─ ACK "accepted" (instant) ───────────────────────────────── │
  │                               │    background: LLM work       │
  │                               │    → upload payload to UOMP   │
  │                               │◄── submit_result(payload_url) │
  │                               │                               │
  ├── poll getJob() ─────────────►│                               │
  │◄─ SUBMITTED ─────────────────│                               │
  │                               │                               │
  ├── read payload_url ──────────►│ (from Policy.JobInitialised)  │
  ├── download via UOMP ──────────────────────────────────────── │
  │◄─ Markdown report ────────────────────────────────────────── │
  │                               │                               │
  └── settle ────────────────────►│ escrow released to seller     │
```

### UOMP: User-Owned Memory Protocol

UOMP solves two problems that ERC-8183 alone cannot:

**Problem 1 — User data sovereignty**: The buyer's portfolio holdings and risk preferences are personal data. They should not be sent verbatim to an unknown agent or stored on a public blockchain. UOMP stores this data in the user's own Memory Guard, and grants the agent time-limited, scoped read access via a Capability Token.

**Problem 2 — Deliverable delivery without a public IP**: After the agent generates the report, how does the buyer get it? If the agent stores it locally (e.g. `http://localhost:9000/...`), the buyer can only access it from the same machine. UOMP's **Payload layer** solves this: the agent uploads the report to the UOMP Gateway, and the buyer downloads it from the Gateway. Neither side needs a public IP.

---

## 2. Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        UOMP Infrastructure                       │
│                                                                  │
│  ┌─────────────────────┐         ┌──────────────────────────┐   │
│  │   Memory Guard      │         │      UOMP Gateway        │   │
│  │   (user's device    │         │  (cloud relay, no IP     │   │
│  │    or local server) │         │   required on either     │   │
│  │                     │         │   side)                  │   │
│  │  portfolio:holdings │         │                          │   │
│  │  profile:risk       │         │  payload.upload()  ──────┼── seller
│  │  ... (user data)    │         │  payload.download()  ────┼── buyer
│  └──────────┬──────────┘         └──────────────────────────┘   │
│             │ Capability Token                                    │
│             ▼ (scoped, time-limited)                             │
│        Agent reads memory                                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     BSC Testnet (chain 97)                       │
│                                                                  │
│  AgenticCommerce        EvaluatorRouter      OptimisticPolicy    │
│  0xa206c051...          0xd7d36d66...        0x4f4678d4...       │
│                                                                  │
│  job lifecycle          settle/dispute       optParams store     │
│  (create/fund/submit)                        (payload_url)       │
└─────────────────────────────────────────────────────────────────┘

┌───────────────────────────┐    ┌──────────────────────────────┐
│        Seller Agent        │    │        Buyer Client           │
│   stockanalyst/app/agent/  │    │   buyer-client/ (TypeScript)  │
│                            │    │   test_e2e.py  (Python)       │
│   main.py      A2A :9000   │    │                              │
│   seller_core  protocol    │    │  1. read memory from UOMP    │
│   signing.py   sign only   │    │  2. negotiate (A2A)          │
│   tools.py     yfinance    │    │  3. fund on-chain            │
│   analysis.py  indicators  │    │  4. notify_funded (A2A)      │
│   managed_model Kimi LLM   │    │  5. poll chain               │
│                            │    │  6. download payload (UOMP)  │
│   generates report         │    │  7. settle                   │
│   uploads to UOMP          │    │                              │
└───────────────────────────┘    └──────────────────────────────┘
```

### UOMP SDK Layers

The UOMP SDK (`@uomp/sdk`) has four layers used in this project:

```
UompClient
├── memory      — read portfolio holdings and risk profile
│               get(key), getByTag(tag)
│
├── payload     — upload/download deliverables
│               upload(data) → payload_id
│               download(id) → Buffer
│
├── session     — access control lifecycle
│               submitDeletionProof()  ← call after reading sensitive memory
│               close()
│
└── transport   — how data moves (auto-detected from baseUrl)
                http://...   → direct (dev: Memory Guard on localhost)
                https://...  → UOMP Gateway + mTLS
```

### Transport and Gateway Modes

| Mode | `UOMP_BASE_URL` | When to use |
|------|-----------------|-------------|
| **Local dev** | `http://127.0.0.1:9374` | Memory Guard running locally; buyer and seller on same machine |
| **UOMP Gateway** | `https://gateway.uomp.org` | Production; buyer/seller have no public IP; Gateway relays everything |
| **Self-hosted Gateway** | `https://my-gateway.example.com` | Enterprise; bring your own relay with mTLS cert |

**Why neither side needs a public IP in Gateway mode:**

```
Seller (no public IP)                   Buyer (no public IP)
       │                                       │
       │  upload(report)                       │  download(payload_id)
       ▼                                       ▼
  ┌─────────────────────────────────────────────┐
  │            UOMP Gateway                     │
  │   (cloud-hosted, accessible to both)        │
  └─────────────────────────────────────────────┘
```

The payload_id returned by `upload()` is stored on-chain in the job's `optParams` (via the OptimisticPolicy `JobInitialised` event). The buyer reads it from chain and calls `download(payload_id)`. No direct connectivity between buyer and seller is ever needed.

### Deliverable Flow: Dev vs Production

**Current implementation — local dev mode only:**
```
Seller: write report to /tmp/bnbagent-deliverables/job-N.json
        submit url = http://localhost:9000/erc8183/job/N/response

Buyer:  GET http://localhost:9000/erc8183/job/N/response
        ← Markdown report

Limitation: only works when buyer and seller are on the same machine.
```

**Production mode — UOMP Payload:**
```typescript
// Seller (signing.py / submit_result side) — conceptual
const payloadId = await uomp.payload.upload(markdownReport);
// submit payloadId on-chain as the deliverable reference

// Buyer (buyer-client/src/index.ts) — production
import { UompClient } from '@uomp/sdk';
const uomp = new UompClient({ token: UOM_TOKEN, baseUrl: UOMP_BASE_URL });
const report = await uomp.payload.download(payloadId);
await uomp.session.submitDeletionProof();
```

### File Structure

```
stockanalyst/
├── app/agent/
│   ├── main.py            A2A server (:9000); in dev, also serves /erc8183/job/{id}/response
│   ├── seller_core.py     negotiate / notify_funded logic + background delivery
│   ├── signing.py         all on-chain signing (quote sign, submit, settle)
│   ├── tools.py           LLM read-only tools: get_stock_quote, get_technical_signals
│   ├── analysis.py        yfinance data + RSI/MACD/Bollinger computation
│   ├── managed_model.py   LLM adapter (Kimi via OpenAI-compatible API)
│   ├── executor.py        A2A wire layer (SellerAgentExecutor)
│   ├── agent_card.py      A2A agent card
│   ├── studio.toml        config: wallet, LLM, pricing, storage
│   └── pyproject.toml
├── test_e2e.py            Python buyer E2E test (Option A)
└── .studio/               wallet keystore — never commit

../buyer-client/
├── src/
│   ├── index.ts           main E2E runner (7 steps)
│   ├── uomp.ts            UOMP context layer (LocalUserMemory stub in dev;
│   │                      replace with UompClient for production)
│   ├── negotiate.ts       A2A negotiate + buildJobDescription + notifyFunded
│   ├── erc8183.ts         on-chain buyer ops (ethers.js v6)
│   └── abi/               contract ABIs
└── package.json
```

### BSC Testnet Contract Addresses

| Contract | Address |
|----------|---------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **MegaFuel Paymaster**: The BSC testnet public paymaster accepts transactions but never confirms them. Both buyer implementations disable it; transactions pay gas directly (~3 gwei).

---

## 3. E2E Testing

### Dev Mode vs Production Mode

| | Dev mode (current) | Production mode |
|--|--|--|
| UOMP memory | `LocalUserMemory` (hardcoded demo data) | `UompClient` + Memory Guard/Gateway |
| Deliverable delivery | Agent serves `localhost:9000` | Agent uploads to UOMP Payload; buyer downloads |
| Buyer/seller on same machine? | Required | Not required |
| Public IP needed? | No (localhost only) | No (Gateway relays) |

All testing below uses **dev mode**.

### Prerequisites

1. **Wallet** — same address used as both buyer and seller for self-testing:
   - tBNB (gas): [BSC Testnet Faucet](https://testnet.bnbchain.org/faucet-smart)
   - U token (payment): [U Token Faucet](https://united-coin-u.github.io/u-faucet/)
   - Need ≥ 0.01 tBNB and ≥ 1.0 U

2. **Kimi API key**: [platform.moonshot.cn](https://platform.moonshot.cn)

3. **Install**:
   ```bash
   cd stockanalyst
   python -m venv app/agent/.venv
   app/agent/.venv/bin/pip install -e ./app/agent
   ```

---

### Start the Seller Agent

**`app/agent/studio.toml`** — LLM config (already set):
```toml
[llm]
provider = "openai"
model    = "moonshot-v1-32k"
base_url = "https://api.moonshot.cn/v1"

[storage]
kind = "local"
```

**`.studio/.env.local`** — secrets (create this file, never commit it):
```bash
WALLET_PASSWORD=<your-keystore-password>
OPENAI_API_KEY=<your-kimi-api-key>

# Required for local storage delivery:
# main.py serves deliverables at /erc8183/job/{id}/response
# so the buyer can download without a separate file server.
ERC8183_AGENT_URL=http://localhost:9000/erc8183
STORAGE_LOCAL_PATH=/tmp/bnbagent-deliverables
```

**Start:**
```bash
# From stockanalyst/
mkdir -p /tmp/bnbagent-deliverables
app/agent/.venv/bin/bag dev
```

**Verify (another terminal):**
```bash
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"message/send",
    "params":{"message":{"role":"user","messageId":"ping",
      "parts":[{"kind":"data","data":{
        "skill":"negotiate",
        "task_description":"Analyze AAPL",
        "terms":{"deliverables":"Report","quality_standards":"Real data"}
      }}]}}
  }'
```

Expected: `"accepted": true` in the response.

---

### Option A — Python Buyer (`test_e2e.py`)

Uses `bnbagent_studio_core` SDK directly. No UOMP context layer — task description is hardcoded.

```bash
# From stockanalyst/, with bag dev running in another terminal
app/agent/.venv/bin/python test_e2e.py
```

**Steps:**

| Step | Action | Detail |
|------|--------|--------|
| 1 | `negotiate` | POST A2A `skill=negotiate` → signed quote 1.0 U |
| 2 | `buy_workflow()` | createJob → registerJob → setBudget → approve → fund (5 txs) |
| 3 | `notify_funded` | POST A2A `skill=notify_funded` → ACK instant; background: LLM work → submit |
| 4 | poll `getJob()` | every 15s; FUNDED → SUBMITTED |
| 5 | `fetch_workflow()` | read deliverable URL from Policy event; download report |
| 6 | `settle_workflow()` | `router.settle()` — escrow released to seller |

---

### Option B — TypeScript UOMP Buyer (`buyer-client/`)

Adds the UOMP context layer: before negotiating, reads portfolio holdings and risk profile to build a personalized task description.

#### Install

```bash
cd ../buyer-client
npm install
```

#### Configure `.env`

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67.json
WALLET_PASSWORD=<your-keystore-password>
AGENT_ENDPOINT=http://localhost:9000
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67

# UOMP (dev mode: not needed; production: set these)
# UOM_TOKEN=<capability-token-from-memory-guard>
# UOMP_BASE_URL=http://127.0.0.1:9374        # local Memory Guard
# UOMP_BASE_URL=https://gateway.uomp.org     # UOMP Gateway (no public IP needed)
```

#### Run

```bash
# From buyer-client/, with stockanalyst/bag dev running
npm run dev
```

**Steps:**

| Step | Action | Detail |
|------|--------|--------|
| 1 | Load UOMP context | `LocalUserMemory` → holdings (AAPL 50, NVDA 20), risk=moderate |
| | | builds: `"Comprehensive stock analysis for AAPL, NVDA (moderate risk profile)"` |
| 2 | `negotiate` | POST A2A → signed quote 1.0 U |
| 3 | On-chain buy | createJob → registerJob → setBudget → approve → fund |
| 4 | `notify_funded` | POST A2A → ACK instant; background: LLM → submit on-chain |
| 5 | Poll chain | `getJob()` every 15s, up to 30 min; FUNDED → SUBMITTED |
| 6 | Fetch report | parse `Policy.JobInitialised` optParams → `deliverable_url` → download |
| 7 | `settle` | `router.settle()` → escrow released |

#### Upgrading from Dev to Production UOMP

**Step 1 — replace `LocalUserMemory` with `UompClient`** in `src/uomp.ts`:

```typescript
// Dev (current) — hardcoded demo data, no actual UOMP server
import { LocalUserMemory } from './uomp.js';
const memory = new LocalUserMemory();

// Production — real user data from Memory Guard or Gateway
import { UompClient } from '@uomp/sdk';
const uomp = new UompClient({
  token: process.env.UOM_TOKEN,
  baseUrl: process.env.UOMP_BASE_URL,  // http://127.0.0.1:9374 or https://gateway.uomp.org
});
const holdings = await uomp.memory.getByTag('portfolio:holdings');
const [riskItem] = await uomp.memory.getByTag('profile:risk');
// ... after reading:
await uomp.session.submitDeletionProof();
```

**Step 2 — use `uomp.payload.download()` to fetch the deliverable** instead of direct HTTP:

```typescript
// Dev (current) — direct HTTP to localhost:9000
const resp = await fetch(deliverableUrl);
const report = await resp.text();

// Production — download from UOMP Gateway (works without public IP on either side)
const reportBuffer = await uomp.payload.download(payloadId);
const report = reportBuffer.toString('utf8');
```

**Step 3 — seller side** (future work): replace `LocalStorageProvider` with `uomp.payload.upload()` in `signing.py`/`submit_result`. The `payload_id` becomes the on-chain deliverable reference.

---

## Pricing

| Stocks | Price |
|--------|-------|
| Any count | **1.0 U** (testnet) |
| Floor / Ceiling | 0.5 U – 5.0 U |

Currency: U token on BSC testnet (`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`)

---

## CI

[CI Workflow](.github/workflows/ci.yml) runs on every push:
- `ruff check` — zero tolerance
- yfinance real data test (AAPL RSI / MACD / Bollinger)
- LLM tool registration check

---

---

<a name="中文"></a>

# 中文

## 1. 原理介绍

### 两个协议，一个流程

本项目连接了两个独立协议：

| 协议 | 角色 | 负责内容 |
|------|------|---------|
| **ERC-8183** | 商业协议 | 链上 Job 创建、资金托管、付款结算 |
| **UOMP** | 用户上下文 + 交付 | 持仓数据、可交付物上传/下载 |

两者职责不同、可独立使用，但组合后实现完整的"个性化 AI 服务购买"流程：
- 用户数据在自己手里（UOMP）
- 付款无需信任中间方（ERC-8183）
- 买卖双方都不需要公网 IP（UOMP Gateway 负责交付）

### ERC-8183：链上商业协议

ERC-8183（AgenticCommerce）把购买 AI 服务变成可验证的链上交易。买家的钱锁在合约里，Agent 提交可验证的可交付物 URL 到链上后才能收款。

```
买家                         链上合约（BSC 测试网）              卖家 Agent
  │                               │                               │
  ├── negotiate（A2A）────────────────────────────────────────── │
  │◄─ 签名报价（1.0 U）──────────────────────────────────────── │
  │                               │                               │
  ├── createJob ─────────────────►│                               │
  ├── registerJob ───────────────►│                               │
  ├── setBudget + fund ──────────►│  U token 锁入托管合约         │
  │                               │                               │
  ├── notify_funded（A2A）────────────────────────────────────── │
  │◄─ ACK "accepted"（立即）──────────────────────────────────── │
  │                               │  后台：LLM 分析               │
  │                               │  → 上传报告到 UOMP            │
  │                               │◄ submit_result(payload_url)  │
  │                               │                               │
  ├── 轮询 getJob()──────────────►│                               │
  │◄─ SUBMITTED ─────────────────│                               │
  │                               │                               │
  ├── 读取 payload_url ──────────►│（从 Policy.JobInitialised）   │
  ├── 从 UOMP 下载报告 ───────────────────────────────────────── │
  │◄─ Markdown 报告 ──────────────────────────────────────────── │
  │                               │                               │
  └── settle ────────────────────►│  托管款释放给卖家              │
```

### UOMP：用户自主内存协议

UOMP 解决了 ERC-8183 单独无法解决的两个问题：

**问题 1 — 用户数据主权**：买家的持仓和风险偏好是隐私数据，不应直接发送给陌生 Agent 或存放在公链上。UOMP 把这些数据存在用户自己的 Memory Guard 里，通过 Capability Token 向 Agent 授予有时限、有范围的读权限。

**问题 2 — 没有公网 IP 时如何交付**：Agent 生成报告后，买家怎么拿到它？如果 Agent 存在本地（如 `http://localhost:9000/...`），买家只有在同一台机器上才能访问。UOMP 的 **Payload 层**解决了这个问题：Agent 把报告上传到 UOMP Gateway，买家从 Gateway 下载。双方都不需要公网 IP。

---

## 2. 架构设计

### 系统总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        UOMP 基础设施                             │
│                                                                  │
│  ┌─────────────────────┐         ┌──────────────────────────┐   │
│  │   Memory Guard      │         │      UOMP Gateway        │   │
│  │  （用户设备或本地）   │         │  （云端中继，双方均无需   │   │
│  │                     │         │    公网 IP）              │   │
│  │  portfolio:holdings │         │                          │   │
│  │  profile:risk       │         │  payload.upload()  ──────┼── 卖家上传
│  │  ...（用户私有数据） │         │  payload.download()  ────┼── 买家下载
│  └──────────┬──────────┘         └──────────────────────────┘   │
│             │ Capability Token                                    │
│             ▼（有范围、有时限）                                   │
│        Agent 读取持仓数据                                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     BSC 测试网（chain 97）                        │
│  AgenticCommerce    EvaluatorRouter    OptimisticPolicy          │
│  Job 生命周期        结算/争议          optParams 存储             │
│  0xa206c051...      0xd7d36d66...      0x4f4678d4...             │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐    ┌───────────────────────────────┐
│       卖家 Agent          │    │         买家客户端              │
│  stockanalyst/app/agent/  │    │  buyer-client/（TypeScript）   │
│                           │    │  test_e2e.py（Python）         │
│  main.py     A2A :9000    │    │                               │
│  seller_core 协议逻辑     │    │  1. 从 UOMP 读取持仓上下文     │
│  signing.py  签名（唯一） │    │  2. negotiate（A2A）           │
│  tools.py    yfinance     │    │  3. 链上付款                   │
│  analysis.py 技术指标     │    │  4. notify_funded（A2A）       │
│  managed_model Kimi LLM   │    │  5. 轮询链上状态               │
│                           │    │  6. 从 UOMP 下载报告           │
│  生成报告 → 上传 UOMP     │    │  7. 结算                      │
└──────────────────────────┘    └───────────────────────────────┘
```

### UOMP SDK 四层结构

```
UompClient
├── memory      — 读取持仓和风险偏好
│               get(key)、getByTag(tag)
│
├── payload     — 上传/下载可交付物（报告）
│               upload(data) → payload_id
│               download(id) → Buffer
│
├── session     — 访问控制生命周期
│               submitDeletionProof()  ← 读完敏感数据后调用
│               close()
│
└── transport   — 数据传输方式（根据 baseUrl 自动检测）
                http://...   → 直连（开发：本地 Memory Guard）
                https://...  → UOMP Gateway + mTLS
```

### Transport 与 Gateway 模式

| 模式 | `UOMP_BASE_URL` | 适用场景 |
|------|-----------------|---------|
| **本地开发** | `http://127.0.0.1:9374` | Memory Guard 在本地运行；买卖双方同一台机器 |
| **UOMP Gateway** | `https://gateway.uomp.org` | 生产环境；买卖双方均无公网 IP，Gateway 中继 |
| **自建 Gateway** | `https://my-gateway.example.com` | 企业自建，需要 mTLS 证书 |

**为什么 Gateway 模式下双方都不需要公网 IP：**

```
卖家（无公网 IP）                          买家（无公网 IP）
     │                                          │
     │  upload(report) → payload_id             │  download(payload_id)
     ▼                                          ▼
┌──────────────────────────────────────────────────┐
│              UOMP Gateway                        │
│   （云端，双方均可访问）                           │
│   payload_id 也会写入链上 optParams               │
└──────────────────────────────────────────────────┘
```

payload_id 由 `upload()` 返回，通过链上 `submit_result` 存入 OptimisticPolicy 的 `JobInitialised` 事件的 `optParams`。买家从链上读取 payload_id，再调用 `download(payload_id)` 获取报告。买卖双方之间从不需要直接连接。

### 可交付物流程：开发模式 vs 生产模式

**当前实现 — 仅限本地开发：**
```
卖家：写入 /tmp/bnbagent-deliverables/job-N.json
     提交 url = http://localhost:9000/erc8183/job/N/response

买家：GET http://localhost:9000/erc8183/job/N/response
     ← Markdown 报告

限制：买卖双方必须在同一台机器上。
```

**生产模式 — UOMP Payload：**
```typescript
// 卖家（submit_result 时）
const payloadId = await uomp.payload.upload(markdownReport);
// 将 payloadId 提交到链上作为可交付物引用

// 买家（获取报告时）
import { UompClient } from '@uomp/sdk';
const uomp = new UompClient({ token: UOM_TOKEN, baseUrl: UOMP_BASE_URL });
const report = await uomp.payload.download(payloadId);
await uomp.session.submitDeletionProof();
```

---

## 3. 端到端测试

### 开发模式 vs 生产模式对比

| | 开发模式（当前） | 生产模式 |
|--|--|--|
| UOMP 数据 | `LocalUserMemory`（硬编码演示数据） | `UompClient` + Memory Guard/Gateway |
| 报告交付 | Agent 本地文件服务（localhost:9000） | Agent 上传到 UOMP，买家从 Gateway 下载 |
| 买卖双方同机？ | 必须 | 不需要 |
| 需要公网 IP？ | 不需要（仅 localhost） | 不需要（Gateway 中继） |

以下测试均使用**开发模式**。

### 前置条件

1. **钱包**（买卖双方共用同一地址，用于自测）：
   - tBNB（Gas 费）：[BSC 测试网水龙头](https://testnet.bnbchain.org/faucet-smart)
   - U token（付款）：[U token 水龙头](https://united-coin-u.github.io/u-faucet/)
   - 需要 ≥ 0.01 tBNB 和 ≥ 1.0 U

2. **Kimi API Key**：[platform.moonshot.cn](https://platform.moonshot.cn)

3. **安装依赖**：
   ```bash
   cd stockanalyst
   python -m venv app/agent/.venv
   app/agent/.venv/bin/pip install -e ./app/agent
   ```

---

### 启动卖家 Agent

**`app/agent/studio.toml`**（已配置）：
```toml
[llm]
provider = "openai"
model    = "moonshot-v1-32k"
base_url = "https://api.moonshot.cn/v1"

[storage]
kind = "local"
```

**`.studio/.env.local`**（创建此文件，不要提交）：
```bash
WALLET_PASSWORD=<你的 keystore 密码>
OPENAI_API_KEY=<你的 Kimi API Key>

# 本地存储交付物所必需：
# main.py 会在 /erc8183/job/{id}/response 提供文件下载
ERC8183_AGENT_URL=http://localhost:9000/erc8183
STORAGE_LOCAL_PATH=/tmp/bnbagent-deliverables
```

**启动：**
```bash
# 在 stockanalyst/ 目录
mkdir -p /tmp/bnbagent-deliverables
app/agent/.venv/bin/bag dev
```

**验证（另开终端）：**
```bash
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"message/send",
    "params":{"message":{"role":"user","messageId":"ping",
      "parts":[{"kind":"data","data":{
        "skill":"negotiate",
        "task_description":"Analyze AAPL",
        "terms":{"deliverables":"Report","quality_standards":"Real data"}
      }}]}}
  }'
```

返回 `"accepted": true` 则 Agent 正常运行。

---

### 方案 A — Python 买家（`test_e2e.py`）

使用 `bnbagent_studio_core` SDK 直接驱动，无 UOMP 上下文层，任务描述硬编码。

```bash
# 在 stockanalyst/ 目录，另一个终端保持 bag dev 运行
app/agent/.venv/bin/python test_e2e.py
```

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `negotiate` | POST A2A `skill=negotiate` → 签名报价 1.0 U |
| 2 | `buy_workflow()` | createJob → registerJob → setBudget → approve → fund（5 笔链上交易） |
| 3 | `notify_funded` | POST A2A `skill=notify_funded` → 立即 ACK；后台：LLM 工作 → submit |
| 4 | 轮询 `getJob()` | 每 15s 查一次；FUNDED → SUBMITTED |
| 5 | `fetch_workflow()` | 从 Policy 事件读取 URL；下载报告 |
| 6 | `settle_workflow()` | `router.settle()`；托管款释放给卖家 |

---

### 方案 B — TypeScript UOMP 买家（`buyer-client/`）

在协商前读取用户持仓和风险偏好，生成个性化任务描述。当前用 `LocalUserMemory`（本地 stub），生产环境换成真实 UOMP 服务。

#### 安装

```bash
cd ../buyer-client
npm install
```

#### 配置 `.env`

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67.json
WALLET_PASSWORD=<你的 keystore 密码>
AGENT_ENDPOINT=http://localhost:9000
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67

# UOMP（开发模式不需要；生产模式配置以下内容）
# UOM_TOKEN=<从 Memory Guard 获取的 Capability Token>
# UOMP_BASE_URL=http://127.0.0.1:9374        # 本地 Memory Guard
# UOMP_BASE_URL=https://gateway.uomp.org     # UOMP Gateway（无需公网 IP）
```

#### 运行

```bash
# 在 buyer-client/ 目录，保持 stockanalyst/ 的 bag dev 运行
npm run dev
```

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 加载 UOMP 上下文 | `LocalUserMemory` → AAPL（50 股）、NVDA（20 股），risk=moderate |
| | | 生成任务描述：`"Comprehensive stock analysis for AAPL, NVDA (moderate risk profile)"` |
| 2 | `negotiate`（A2A） | POST → 签名报价 1.0 U |
| 3 | 链上买入 | createJob → registerJob → setBudget → approve → fund |
| 4 | `notify_funded`（A2A） | POST → 立即 ACK；后台：LLM → 链上提交 |
| 5 | 轮询链上状态 | `getJob()` 每 15s；最长 30 分钟；FUNDED → SUBMITTED |
| 6 | 获取报告 | 解析 `Policy.JobInitialised` optParams → `deliverable_url` → 下载 |
| 7 | `settle` | `router.settle()`；托管款释放 |

#### 升级到生产 UOMP

**第 1 步 — 替换 `LocalUserMemory`**（`src/uomp.ts`）：

```typescript
// 开发（当前）— 硬编码演示数据，无需 UOMP 服务
import { LocalUserMemory } from './uomp.js';
const memory = new LocalUserMemory();

// 生产 — 从 Memory Guard 或 Gateway 读取真实用户数据
import { UompClient } from '@uomp/sdk';
const uomp = new UompClient({
  token: process.env.UOM_TOKEN,
  baseUrl: process.env.UOMP_BASE_URL,  // 本地 :9374 或 Gateway
});
const holdings = await uomp.memory.getByTag('portfolio:holdings');
const [riskItem] = await uomp.memory.getByTag('profile:risk');
// 读完敏感数据后：
await uomp.session.submitDeletionProof();
```

**第 2 步 — 用 `uomp.payload.download()` 获取报告**（无需公网 IP）：

```typescript
// 开发（当前）— 直连 localhost:9000（买卖双方必须同机）
const resp = await fetch(deliverableUrl);
const report = await resp.text();

// 生产 — 从 UOMP Gateway 下载（双方无需公网 IP）
const reportBuffer = await uomp.payload.download(payloadId);
const report = reportBuffer.toString('utf8');
```

**第 3 步 — 卖家侧**（待实现）：在 `signing.py` 的 `submit_result` 中用 `uomp.payload.upload()` 替换 `LocalStorageProvider`，将 `payload_id` 作为链上可交付物引用。

---

## 定价

| 分析数量 | 价格 |
|----------|------|
| 任意股票数 | **1.0 U**（测试网） |
| 价格区间 | 0.5 U – 5.0 U |

货币：BSC 测试网 U token（`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`）

---

## CI 自动测试

[CI Workflow](.github/workflows/ci.yml) 在每次 push 时运行：
- `ruff check` — 零容忍
- yfinance 真实数据测试（AAPL RSI/MACD/布林带）
- LLM 工具注册验证
