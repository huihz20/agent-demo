# Stock Analysis Agent

[![ERC-8183](https://img.shields.io/badge/Protocol-ERC--8183-blue)](https://github.com/bnb-chain/BEPs)
[![Network](https://img.shields.io/badge/Network-BSC%20Testnet-yellow)](https://testnet.bscscan.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org)

> English | [中文](#中文)

---

## 1. 原理介绍 / How It Works

### ERC-8183：链上 AI 商业协议

ERC-8183 (AgenticCommerce) 是 BNBChain 上的 AI Agent 商业协议。它把"AI 服务购买"这件事变成一笔可验证的链上交易：买家的钱锁在合约里，Agent 交付可验证的结果后才能拿到报酬。整个流程无需信任中间方。

### 本项目做什么

这个 Agent 是一个**股票分析卖家**。买家指定股票代码（如 AAPL、NVDA），付款后 Agent 调用 yfinance 拉取真实行情，用 Kimi LLM 生成包含估值、技术指标、风险评级的 Markdown 报告，提交链上后买家结算付款。

### 完整交易流程

```
买家                          链上合约                         卖家 Agent
 │                               │                               │
 │── negotiate ──────────────────────────────────────────────── │
 │   (A2A JSON-RPC)              │              签名报价 (EIP-191)│
 │◄─ 签名报价 (1.0 U) ──────────────────────────────────────── │
 │                               │                               │
 │── createJob ─────────────────►│                               │
 │── registerJob ───────────────►│ (绑定 OptimisticPolicy)       │
 │── setBudget ─────────────────►│                               │
 │── approve + fund ────────────►│ (U token 锁入托管合约)        │
 │                               │                               │
 │── notify_funded ──────────────────────────────────────────── │
 │   (A2A JSON-RPC)              │              验证链上状态      │
 │◄─ ACK "accepted" ─────────────────────────────────────────── │
 │                               │     ┌─ 后台异步 ─────────────┤
 │                               │     │  LLM 生成分析报告       │
 │                               │     │  写入本地文件           │
 │                               │     │  submit_result ─────────►│
 │                               │◄────┘  (链上提交报告 URL)    │
 │                               │                               │
 │── 轮询 getJob() ─────────────►│                               │
 │◄─ status=SUBMITTED ──────────│                               │
 │                               │                               │
 │── 读取报告 URL ──────────────►│ (从 Policy 事件解析)          │
 │◄─ 下载 Markdown 报告 ─────────────────────────────────────── │
 │                               │                               │
 │── settle ────────────────────►│ (托管款项释放给卖家)          │
```

### 关键设计原则

- **签名从不经过 LLM**：所有链上签名（报价签名、submit、settle）都在 `signing.py` 固定代码里，LLM 只生成分析文本，不碰钱
- **notify_funded 立即 ACK**：Agent 收到通知后马上回复"accepted"，LLM 分析和链上提交在后台异步进行（最长 600s）
- **链是唯一真相**：买家通过轮询链上 job 状态来判断是否交付，不依赖 Agent 的回调
- **Sweep 机制**：每次 notify_funded 同时扫描其他未处理的已付款 job，防遗漏

---

## 2. 架构设计 / Architecture

### 系统组件

```
┌─────────────────────────────────────────────────────────┐
│                    BSC Testnet (chain 97)                │
│                                                         │
│  AgenticCommerce    EvaluatorRouter    OptimisticPolicy  │
│  (job lifecycle)    (settle/dispute)   (optParams store) │
│  0xa206c051...      0xd7d36d66...      0x4f4678d4...     │
└───────────────────────────┬─────────────────────────────┘
                            │ on-chain
        ┌───────────────────┴───────────────────┐
        │                                       │
┌───────▼──────────────────────┐  ┌────────────▼────────────────────┐
│         Seller Agent          │  │          Buyer Client             │
│  (stockanalyst/app/agent/)    │  │  (Option A: test_e2e.py         │
│                               │  │   Option B: buyer-client/)       │
│  main.py       — A2A 服务    │  │                                   │
│  seller_core.py — 协议逻辑   │  │  TypeScript / Python             │
│  signing.py    — 签名 (唯一) │  │  ethers.js v6 / bnbagent SDK    │
│  tools.py      — yfinance    │  │  UOMP 用户持仓上下文 (TS)        │
│  analysis.py   — 技术指标    │  │                                   │
│  managed_model.py — Kimi LLM │  │                                   │
│                               │  │                                   │
│  A2A at http://localhost:9000 │  │  读取同一个 keystore 自测         │
└───────────────────────────────┘  └──────────────────────────────────┘
```

### 文件说明

```
stockanalyst/
├── app/agent/
│   ├── main.py            A2A 入口；本地开发时额外挂载 /erc8183/job/{id}/response
│   ├── seller_core.py     negotiate / notify_funded 协议逻辑 + 后台交付
│   ├── signing.py         所有签名操作（报价签名、submit、settle）
│   ├── tools.py           LLM 只读工具：get_stock_quote, get_technical_signals
│   ├── analysis.py        yfinance 数据拉取 + RSI/MACD/布林带计算
│   ├── managed_model.py   LLM 适配器（Kimi, openai 兼容接口）
│   ├── executor.py        A2A 协议层（SellerAgentExecutor）
│   ├── agent_card.py      A2A Agent Card
│   ├── studio.toml        Agent 配置（钱包、LLM、定价、存储）
│   └── pyproject.toml     依赖
├── test_e2e.py            Python E2E 买家测试脚本
└── .studio/               钱包 keystore（不提交）

../buyer-client/           TypeScript UOMP 买家客户端
├── src/
│   ├── index.ts           主流程（7 步 E2E）
│   ├── uomp.ts            UOMP 用户内存：持仓 + 风险偏好
│   ├── negotiate.ts       A2A negotiate / notifyFunded
│   ├── erc8183.ts         链上操作（ethers.js v6）
│   └── abi/               合约 ABI
└── package.json
```

### BSC 测试网合约地址

| 合约 | 地址 |
|------|------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **注意**：BSC 测试网公共 Paymaster 接受交易但永不确认。两个买家实现均已禁用 Paymaster，所有交易直接自付 gas。

---

## 3. 端到端测试 / E2E Testing

### 前置条件

1. **钱包**：同一个 wallet address 同时作为 seller 和 buyer（测试用）
   - tBNB（用于 gas）：[BSC 测试网水龙头](https://testnet.bnbchain.org/faucet-smart)
   - U token（用于付款）：[U token 水龙头](https://united-coin-u.github.io/u-faucet/)

2. **Kimi API Key**：在 [platform.moonshot.cn](https://platform.moonshot.cn) 获取

3. **依赖安装**：
   ```bash
   # 在 stockanalyst/ 目录
   cd stockanalyst
   python -m venv app/agent/.venv
   app/agent/.venv/bin/pip install -e ./app/agent
   ```

### 配置 Seller Agent

**`app/agent/studio.toml`**（LLM 配置）：
```toml
[llm]
provider = "openai"
model    = "moonshot-v1-32k"
base_url = "https://api.moonshot.cn/v1"

[storage]
kind = "local"    # 可交付物写入本地磁盘
```

**`.studio/.env.local`**（secrets，不提交）：
```bash
WALLET_PASSWORD=<your-keystore-password>
OPENAI_API_KEY=<your-kimi-api-key>
ERC8183_AGENT_URL=http://localhost:9000/erc8183
STORAGE_LOCAL_PATH=/tmp/bnbagent-deliverables
```

> `ERC8183_AGENT_URL` 和 `STORAGE_LOCAL_PATH` 是本地存储模式必须的配置。`main.py` 会在 `/erc8183/job/{id}/response` 路径提供可交付物文件供买家下载。

### 启动 Seller Agent

```bash
# 在 stockanalyst/ 目录
mkdir -p /tmp/bnbagent-deliverables
app/agent/.venv/bin/bag dev
```

Agent 启动后监听 `http://localhost:9000`，日志输出到终端。

**验证 Agent 正常运行**（另开一个终端）：
```bash
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "ping",
        "parts": [{"kind": "data", "data": {
          "skill": "negotiate",
          "task_description": "Analyze AAPL",
          "terms": {"deliverables": "Stock report", "quality_standards": "Real data"}
        }}]
      }
    }
  }' | python3 -m json.tool
```

返回 `"accepted": true` 表示 Agent 正常。

---

### Option A — Python 买家（`test_e2e.py`）

使用 `bnbagent_studio_core` SDK 直接驱动链上操作，6 步完成全流程。

```bash
# 在 stockanalyst/ 目录，保持 bag dev 在另一终端运行
app/agent/.venv/bin/python test_e2e.py
```

**执行步骤**：

```
Step 1: negotiate         ── POST /   skill=negotiate
                             ← 签名报价 1.0 U (EIP-191)

Step 2: buy_workflow()    ── createJob  (链上，tx1)
                             registerJob (tx2)
                             setBudget   (tx3)
                             approve     (tx4)
                             fund        (tx5, U 进入托管)

Step 3: notify_funded     ── POST /   skill=notify_funded, job_id=N
                             ← ACK "accepted" (立即返回)
                               后台: LLM 分析 → submit_result (tx6)

Step 4: 轮询 getJob()      ── 每 15s 查一次链上状态
                             FUNDED → ... → SUBMITTED

Step 5: fetch_workflow()  ── 从链上 Policy 事件读取报告 URL
                             下载 Markdown 报告

Step 6: settle_workflow() ── router.settle() (tx7, 托管款释放给卖家)
```

**预期输出**：
```
══════════════════════════════════════════════
  Stock Analysis Agent — End-to-End Test
══════════════════════════════════════════════

── Step 1: Negotiate ──────────────────────
  ✓ Accepted   price=1.0 U
  ✓ Estimated  completion=600s

── Step 2: Buy ─────────────────────────────
  ✓ Job ID     353
  ✓ fund_tx    0x...

── Step 3: notify_funded ───────────────────
  ✓ Agent ACK  status=accepted

── Step 4: Poll ─────────────────────────────
  [  0s] status=FUNDED
  [ 15s] status=FUNDED
  ...
  [180s] status=SUBMITTED
  ✓ Job reached SUBMITTED

── Step 5: Fetch deliverable ───────────────
  ✓ Deliverable URL: http://localhost:9000/erc8183/job/353/response
  ┌─ REPORT ──────────────────────────────────┐
  │ # Stock Analysis Report                    │
  │ ...                                        │
  └───────────────────────────────────────────┘

── Step 6: Settle ──────────────────────────
  ✓ settle_tx  0x...

══════════════════════════════════════════════
  ✓ E2E test PASSED
══════════════════════════════════════════════
```

---

### Option B — TypeScript UOMP 买家（`buyer-client/`）

集成 UOMP（User-Owned Memory Protocol）用户上下文层的 TypeScript 客户端。在协商前自动从本地内存读取持仓和风险偏好，生成个性化任务描述。

#### 安装

```bash
cd ../buyer-client
npm install
```

#### 配置 `.env`

```bash
cp .env.example .env    # 如果有示例文件
# 或手动创建 .env：
```

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67.json
WALLET_PASSWORD=<your-keystore-password>
AGENT_ENDPOINT=http://localhost:9000
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67
```

> `WALLET_PASSWORD` 仅在本地 `.env` 文件中设置，不要通过命令行或聊天传递。

#### 运行

```bash
# buyer-client/ 目录，保持 stockanalyst/ 的 bag dev 运行
npm run dev
```

**执行步骤（7 步）**：

```
Step 1: 加载 UOMP 上下文
  读取 portfolio:holdings → AAPL (50股), NVDA (20股)
  读取 profile:risk → moderate, 偏好 RSI-14/MACD/布林带
  生成任务描述: "Comprehensive stock analysis for AAPL, NVDA (moderate risk profile)"

Step 2: negotiate (A2A)
  POST http://localhost:9000/   skill=negotiate
  ← 签名报价 1.0 U

Step 3: 链上买入
  createJob  → registerJob → setBudget → approve → fund
  Job ID: N, 1.0 U 进入托管

Step 4: notify_funded (A2A)
  POST http://localhost:9000/   skill=notify_funded, job_id=N
  ← ACK status=accepted (立即)
  后台: LLM 分析 → submit_result (写链上)

Step 5: 轮询链上状态
  每 15s 查 getJob()，最长等 30 分钟
  FUNDED → SUBMITTED

Step 6: 获取报告
  查 Policy.JobInitialised 事件 → 解析 optParams → 取 deliverable_url
  GET http://localhost:9000/erc8183/job/N/response → Markdown 报告

Step 7: settle
  router.settle() → 托管款释放给卖家
```

**UOMP 上下文说明**：

`src/uomp.ts` 实现了 `@uomp/sdk` 接口的本地版本，预置了演示数据。生产环境替换为：
```typescript
import { UserMemory } from "@uomp/sdk";
const memory = new UserMemory({ token: process.env.UOM_TOKEN });
```

---

## 定价

| 分析数量 | 价格 |
|----------|------|
| 任意股票数 | **1.0 U**（测试网） |
| 价格区间 | 0.5 U – 5.0 U |

货币：BSC 测试网 U token（`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`）

---

## CI

[CI Workflow](.github/workflows/ci.yml) 在每次 push 时运行：
- `ruff check` 代码检查
- yfinance 真实数据测试（AAPL RSI/MACD/布林带）
- LLM 工具注册验证

---

<a name="中文"></a>

---

# English

## 1. How It Works

### ERC-8183: On-Chain Commerce for AI Agents

ERC-8183 (AgenticCommerce) is BNBChain's commerce protocol for AI agents. It turns "buying AI services" into a verifiable on-chain transaction: the buyer's payment is locked in a smart contract escrow, and the agent is paid only after delivering a verifiable result — no trusted intermediary required.

### What This Agent Does

This agent is a **stock analysis seller**. A buyer specifies stock tickers (e.g. AAPL, NVDA), pays into escrow, and the agent fetches live market data via yfinance, uses Kimi LLM to generate a Markdown report with valuations, technical indicators, and risk ratings, then submits the report URL on-chain. The buyer settles payment after receiving the report.

### End-to-End Flow

```
Buyer                        On-Chain Contracts               Seller Agent
  │                               │                               │
  │── negotiate ──────────────────────────────────────────────── │
  │   (A2A JSON-RPC)              │              sign quote (EIP-191)
  │◄─ signed quote (1.0 U) ───────────────────────────────────── │
  │                               │                               │
  │── createJob ─────────────────►│                               │
  │── registerJob ───────────────►│ (bind OptimisticPolicy)       │
  │── setBudget ─────────────────►│                               │
  │── approve + fund ────────────►│ (U token locked in escrow)    │
  │                               │                               │
  │── notify_funded ──────────────────────────────────────────── │
  │   (A2A JSON-RPC)              │              verify on-chain  │
  │◄─ ACK "accepted" ─────────────────────────────────────────── │
  │                               │     ┌─ background async ─────┤
  │                               │     │  LLM generates report   │
  │                               │     │  write to local file    │
  │                               │     │  submit_result ─────────►│
  │                               │◄────┘  (report URL on-chain) │
  │                               │                               │
  │── poll getJob() ─────────────►│                               │
  │◄─ status=SUBMITTED ──────────│                               │
  │                               │                               │
  │── read report URL ───────────►│ (parse Policy event)          │
  │◄─ download Markdown ──────────────────────────────────────── │
  │                               │                               │
  │── settle ────────────────────►│ (escrow released to seller)   │
```

### Key Design Principles

- **Signing never in the LLM**: All on-chain signing (quote signing, submit, settle) is in `signing.py` fixed code. The LLM only generates the analysis text and never touches money.
- **notify_funded ACKs immediately**: The agent responds "accepted" right away; LLM work and on-chain submission happen asynchronously in the background (up to 600s).
- **The chain is the source of truth**: The buyer polls on-chain job status to confirm delivery — no reliance on agent callbacks.
- **Sweep mechanism**: Every `notify_funded` also scans for other unfulfilled funded jobs, preventing missed deliveries.

---

## 2. Architecture

### System Components

```
┌─────────────────────────────────────────────────────────┐
│                    BSC Testnet (chain 97)                │
│                                                         │
│  AgenticCommerce    EvaluatorRouter    OptimisticPolicy  │
│  (job lifecycle)    (settle/dispute)   (optParams store) │
└───────────────────────────┬─────────────────────────────┘
                            │ on-chain
        ┌───────────────────┴───────────────────┐
        │                                       │
┌───────▼──────────────────────┐  ┌────────────▼────────────────────┐
│         Seller Agent          │  │          Buyer Client             │
│  (stockanalyst/app/agent/)    │  │  (Option A: test_e2e.py)        │
│                               │  │  (Option B: buyer-client/)      │
│  main.py       A2A server    │  │                                   │
│  seller_core.py protocol     │  │  TypeScript / Python              │
│  signing.py    signing only  │  │  ethers.js v6 / bnbagent SDK     │
│  tools.py      yfinance      │  │  UOMP user context (TS)          │
│  analysis.py   indicators    │  │                                   │
│  managed_model.py Kimi LLM   │  │  Uses same wallet for self-test  │
│                               │  │                                   │
│  A2A at :9000                │  │                                   │
└───────────────────────────────┘  └──────────────────────────────────┘
```

### BSC Testnet Contract Addresses

| Contract | Address |
|----------|---------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **Note**: The BSC testnet public MegaFuel paymaster accepts transactions but never confirms them. Both buyer implementations disable the paymaster and pay gas directly.

---

## 3. E2E Testing

### Prerequisites

1. **Wallet**: The same wallet address is used as both seller and buyer for self-testing.
   - tBNB (gas): [BSC Testnet Faucet](https://testnet.bnbchain.org/faucet-smart)
   - U token (payment): [U Token Faucet](https://united-coin-u.github.io/u-faucet/)

2. **Kimi API Key**: Get one at [platform.moonshot.cn](https://platform.moonshot.cn)

3. **Install dependencies**:
   ```bash
   cd stockanalyst
   python -m venv app/agent/.venv
   app/agent/.venv/bin/pip install -e ./app/agent
   ```

### Configure the Seller Agent

**`app/agent/studio.toml`** (LLM config):
```toml
[llm]
provider = "openai"
model    = "moonshot-v1-32k"
base_url = "https://api.moonshot.cn/v1"

[storage]
kind = "local"
```

**`.studio/.env.local`** (secrets — never commit):
```bash
WALLET_PASSWORD=<your-keystore-password>
OPENAI_API_KEY=<your-kimi-api-key>
ERC8183_AGENT_URL=http://localhost:9000/erc8183
STORAGE_LOCAL_PATH=/tmp/bnbagent-deliverables
```

> `ERC8183_AGENT_URL` and `STORAGE_LOCAL_PATH` are required for local storage mode. `main.py` serves deliverable files at `/erc8183/job/{id}/response` so the buyer can download the report.

### Start the Seller Agent

```bash
# From stockanalyst/
mkdir -p /tmp/bnbagent-deliverables
app/agent/.venv/bin/bag dev
```

The agent listens at `http://localhost:9000`. Logs print to the terminal.

**Verify the agent is running** (in another terminal):
```bash
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "ping",
        "parts": [{"kind": "data", "data": {
          "skill": "negotiate",
          "task_description": "Analyze AAPL",
          "terms": {"deliverables": "Stock report", "quality_standards": "Real data"}
        }}]
      }
    }
  }'
```

Expected: `"accepted": true` in the response.

---

### Option A — Python Buyer (`test_e2e.py`)

Uses the `bnbagent_studio_core` SDK to drive the full 6-step flow directly.

```bash
# From stockanalyst/, with bag dev running in another terminal
app/agent/.venv/bin/python test_e2e.py
```

**Steps**:

```
Step 1: negotiate        POST /  skill=negotiate
                         ← signed quote 1.0 U (EIP-191)

Step 2: buy_workflow()   createJob (tx1) → registerJob (tx2)
                         → setBudget (tx3) → approve (tx4) → fund (tx5)
                         U token locked in escrow

Step 3: notify_funded    POST /  skill=notify_funded, job_id=N
                         ← ACK "accepted" (immediate)
                           background: LLM → submit_result (tx6)

Step 4: poll getJob()    every 15s, FUNDED → SUBMITTED

Step 5: fetch_workflow() read report URL from Policy event
                         download Markdown report

Step 6: settle_workflow() router.settle() (tx7), escrow released
```

---

### Option B — TypeScript UOMP Buyer (`buyer-client/`)

A standalone TypeScript client with a UOMP (User-Owned Memory Protocol) context layer. Before negotiating, it reads portfolio holdings and risk profile from local memory to build a personalized task description.

#### Setup

```bash
cd ../buyer-client
npm install
```

Create `.env`:
```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67.json
WALLET_PASSWORD=<your-keystore-password>
AGENT_ENDPOINT=http://localhost:9000
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67
```

#### Run

```bash
# From buyer-client/, with stockanalyst/bag dev running
npm run dev
```

**Steps**:

```
Step 1: Load UOMP context
  portfolio:holdings → AAPL (50 shares), NVDA (20 shares)
  profile:risk → moderate, prefers RSI-14/MACD/Bollinger Bands
  builds task: "Comprehensive stock analysis for AAPL, NVDA (moderate risk profile)"

Step 2: negotiate (A2A)
  POST http://localhost:9000/  skill=negotiate
  ← signed quote 1.0 U

Step 3: on-chain buy
  createJob → registerJob → setBudget → approve → fund
  Job ID: N, 1.0 U locked in escrow

Step 4: notify_funded (A2A)
  POST http://localhost:9000/  skill=notify_funded, job_id=N
  ← ACK status=accepted (immediate)
  background: LLM analysis → submit_result → on-chain

Step 5: poll on-chain status
  getJob() every 15s, up to 30 minutes
  FUNDED → SUBMITTED

Step 6: fetch report
  query Policy.JobInitialised event → parse optParams → deliverable_url
  GET http://localhost:9000/erc8183/job/N/response → Markdown report

Step 7: settle
  router.settle() → escrow released to seller
```

**UOMP context**: `src/uomp.ts` implements the `@uomp/sdk` interface locally with demo data. For production, replace with:
```typescript
import { UserMemory } from "@uomp/sdk";
const memory = new UserMemory({ token: process.env.UOM_TOKEN });
```

---

## Pricing

| Stocks | Price |
|--------|-------|
| Any count | **1.0 U** (testnet) |
| Floor / Ceiling | 0.5 U – 5.0 U |

Currency: U token on BSC testnet (`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`)
