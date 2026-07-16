# Stock Analysis Agent · 股票分析 Agent

[![BNBChain AI Studio](https://img.shields.io/badge/BNBChain-AI%20Studio-F0B90B?logo=binance&logoColor=white)](https://www.bnbchain.org)
[![ERC-8183](https://img.shields.io/badge/Protocol-ERC--8183-blue)](https://github.com/bnb-chain/BEPs)
[![Network](https://img.shields.io/badge/Network-BSC%20Testnet-yellow)](https://testnet.bscscan.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Google ADK](https://img.shields.io/badge/Framework-Google%20ADK-4285F4?logo=google&logoColor=white)](https://github.com/google/adk-python)
[![License](https://img.shields.io/badge/License-MIT-green)](../../LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/huihzhao/agent-demo/ci.yml?branch=main&label=CI&logo=github)](https://github.com/huihzhao/agent-demo/actions)

---

> **English** | [中文](#中文)

## Overview

A professional stock analysis agent deployed on BNBChain AI Studio, built with the ERC-8183 commerce protocol. Buyers submit a list of stock symbols and receive a comprehensive Markdown report — valuation, technical signals, and risk ratings — backed by real market data fetched via [yfinance](https://github.com/ranaroussi/yfinance).

### How it works

```
Buyer → negotiate (signed quote: 1.0 U) → fund on-chain
      → notify_funded → Agent fetches live data + runs LLM
      → Markdown report written locally → result submitted on-chain
      → Buyer reads deliverable URL from chain → settle
```

### What gets analyzed

| Signal | Source |
|--------|--------|
| Price, PE, PB, Market Cap | yfinance (real-time) |
| Analyst target & recommendation | yfinance |
| RSI-14 | Computed from 6-month history |
| MACD + crossover signal | EMA-12/26/9 |
| Bollinger Bands (20-day) | Price position in band |
| 1M / 3M momentum | Historical price delta |

---

## Quick Start

### Prerequisites

- Python 3.12+
- `bnbagent-studio` CLI: `uv tool install bnbagent-studio`
- BSC testnet wallet with tBNB (gas) + U token
- Kimi API key (get one at [platform.moonshot.cn](https://platform.moonshot.cn))

### Configuration

**LLM (Kimi)** — set in `app/agent/studio.toml`:

```toml
[llm]
provider = "openai"
model    = "moonshot-v1-32k"
base_url = "https://api.moonshot.cn/v1"
```

Add the API key to `.studio/.env.local` (never commit this file):

```bash
OPENAI_API_KEY=sk-...your-kimi-key...
WALLET_PASSWORD=your-keystore-password
```

**Storage** — deliverables are written to local disk (`kind = "local"` in `studio.toml`).
The deliverable URL stored on-chain points to the local path; for production use
configure an IPFS pinning service instead.

### Start the agent

```bash
# From stockanalyst/
app/agent/.venv/bin/bag dev        # A2A server on http://localhost:9000
```

### Smoke test (negotiate only)

```bash
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "test-01",
        "parts": [{"kind": "data", "data": {
          "skill": "negotiate",
          "task_description": "Analyze AAPL, NVDA",
          "terms": {
            "deliverables": "Stock analysis report in Markdown",
            "quality_standards": "Real market data, RSI, MACD, fundamental analysis"
          }
        }}]
      }
    }
  }'
```

> The agent serves A2A JSON-RPC at `/`. The `skill` field in the data part is required.

---

## E2E Test Flow

The full ERC-8183 buyer flow has 6–7 steps:

```
1. negotiate         → signed price quote (1 U)
2. createJob         → on-chain job created
3. registerJob       → bind OptimisticPolicy
4. setBudget         → set escrow amount
5. approve + fund    → U token deposited to escrow
6. notify_funded     → agent starts LLM analysis
7. poll + fetch      → wait for SUBMITTED, read report
8. settle            → release escrow to seller
```

Two buyer implementations are provided. Both use the **same wallet** as the seller
for self-testing on BSC testnet.

### BSC testnet contract addresses

| Contract | Address |
|----------|---------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **MegaFuel Paymaster:** The BSC testnet public paymaster accepts transactions but
> they never confirm. Both clients disable it and pay gas directly (~0.1 gwei).

---

### Option A — Python buyer (`test_e2e.py`)

Uses `bnbagent_studio_core` buyer APIs directly.

**Prerequisites:** `bag dev` running, wallet funded with tBNB + U token.

```bash
# From stockanalyst/
app/agent/.venv/bin/python test_e2e.py
```

**What it does:**

```
Step 1: A2A negotiate          → price=1 U, signed quote
Step 2: buy_workflow()         → createJob → registerJob → setBudget → approve → fund
Step 3: notify_funded (A2A)    → agent ACK "delivery started"
Step 4: poll getJob()          → FUNDED → SUBMITTED
Step 5: fetch_workflow()       → reads deliverable URL from chain, downloads report
Step 6: settle_workflow()      → router.settle(), escrow released
```

The script monkey-patches the MegaFuel paymaster out so all transactions
self-pay gas. It calls the agent's A2A endpoint directly for negotiate and
notify_funded (the SDK's `negotiate_with_seller` targets a REST `/negotiate`
endpoint, but this agent serves JSON-RPC at `/`).

---

### Option B — TypeScript UOMP buyer (`buyer-client/`)

A standalone TypeScript client that integrates the UOMP (User-Owned Memory
Protocol) context layer. Located at `../buyer-client/` relative to this directory.

**What UOMP adds:** Before negotiating, the client loads portfolio holdings
(`portfolio:holdings` tag) and risk profile (`profile:risk` tag) from local memory,
then builds a personalized task description automatically.

#### Setup

```bash
cd ../buyer-client
npm install
```

Configure `.env`:

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67.json
WALLET_PASSWORD=your-keystore-password
AGENT_ENDPOINT=http://localhost:9000
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67
```

#### Run

```bash
# buyer-client/ directory, with bag dev running in stockanalyst/
npm run dev
```

**What it does (7 steps):**

```
Step 1: Load UOMP context    → AAPL/NVDA holdings + moderate risk profile
Step 2: negotiate (A2A)      → price=1 U, signed quote
Step 3: On-chain buy         → createJob → registerJob → setBudget → approve → fund
Step 4: notify_funded (A2A)  → agent ACK status=accepted
Step 5: poll getJob()        → FUNDED → SUBMITTED
Step 6: fetch deliverable    → read URL from Policy.JobInitialised event, download report
Step 7: settle               → router.settle(), escrow released
```

The client loads the wallet from the encrypted keystore (same file as the agent),
uses ethers.js v6 for all on-chain operations, and decodes the `JobCreated` event
directly from the transaction receipt (no `eth_getLogs` call — avoids BSC testnet
rate limits).

#### UOMP context

```typescript
// src/uomp.ts — mirrors @uomp/sdk interface
// Pre-populated holdings: AAPL (50 shares, moderate) + NVDA (30 shares, growth)
// Risk profile: moderate — max 15% per position, focus on fundamentals + technicals

// In production: replace LocalUserMemory with:
// import { UserMemory } from "@uomp/sdk";
// const memory = new UserMemory({ token: UOM_TOKEN });
```

---

## Project Structure

```
stockanalyst/
├── app/agent/
│   ├── analysis.py        # Stock data engine — yfinance + RSI/MACD/Bollinger
│   ├── tools.py           # LLM-callable read-only tools (get_stock_quote, get_technical_signals)
│   ├── seller_core.py     # ERC-8183 seller logic — negotiate / notify_funded / fulfill
│   ├── signing.py         # Deterministic signing — quote-sign / submit / settle
│   ├── main.py            # A2A entrypoint on 0.0.0.0:9000
│   ├── executor.py        # A2A wire (SellerAgentExecutor)
│   ├── managed_model.py   # LLM adapter (currently Kimi via OpenAI-compatible API)
│   ├── agent_card.py      # A2A agent card builder
│   ├── studio.toml        # Agent config (wallet, LLM, pricing, storage)
│   └── pyproject.toml     # Dependencies
├── test_e2e.py            # Python E2E buyer test
└── .studio/               # Wallet keystore — NEVER committed

../buyer-client/
├── src/
│   ├── index.ts           # Main E2E runner (7 steps)
│   ├── uomp.ts            # UOMP context layer (portfolio + risk profile)
│   ├── negotiate.ts       # A2A negotiate + buildJobDescription + notifyFunded
│   ├── erc8183.ts         # On-chain buyer operations (ethers.js v6)
│   └── abi/               # Contract ABIs (commerce, router, policy, erc20)
└── package.json
```

---

## Pricing

| Symbols | Price |
|---------|-------|
| Any count | **1.0 U** (testnet) |
| Floor / Ceiling | 0.5 U – 5.0 U |

Currency: `$U` token on BSC testnet (`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`)

---

## Unit Tests

```bash
cd stockanalyst

# Analysis engine — fetches real market data from yfinance
app/agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'app/agent')
from analysis import fetch_quote, fetch_technical_signals
q = fetch_quote('AAPL')
print(f'Price: {q[\"price\"]}  PE: {q[\"pe_ratio\"]}  Sector: {q[\"sector\"]}')
t = fetch_technical_signals('AAPL')
print(f'RSI-14: {t[\"rsi_14\"]}  MACD: {t[\"macd\"][\"crossover\"]}')
"
```

## CI

The [CI workflow](.github/workflows/ci.yml) runs on every push/PR:
- Lint: `ruff check` — zero tolerance
- Analysis engine: fetches real AAPL data, asserts RSI/MACD/Bollinger are present
- Tools load: all LLM tools registered correctly
- Prompt builder: structured prompt generation verified

---

## Deploy (Managed Platform — 48h Testnet Trial)

```bash
bag platform login          # GitHub device flow
bag deploy agent            # build + push arm64 image → deploy
bag deploy status           # check deployment state
```

---

<a name="中文"></a>

---

# 中文

## 概述

基于 BNBChain AI Studio 构建的专业股票分析 Agent，采用 ERC-8183 商业协议。买家提交股票代码列表，Agent 拉取真实行情数据，运行 LLM 深度分析，生成包含估值、技术信号、风险评级的完整 Markdown 报告，结果写入本地并提交链上。

### 工作流程

```
买家 → negotiate（签名报价：1.0 U）→ 链上打款
     → notify_funded → Agent 拉取实时数据 + LLM 分析
     → Markdown 报告写入本地 → 链上提交结果
     → 买家从链上读取可交付物 URL → 结算
```

### 分析内容

| 指标 | 数据源 |
|------|--------|
| 价格、PE、PB、市值 | yfinance（实时） |
| 分析师目标价和建议 | yfinance |
| RSI-14 | 6 个月历史数据计算 |
| MACD + 交叉信号 | EMA-12/26/9 |
| 布林带（20 日） | 价格在通道中的位置 |
| 1M / 3M 动量 | 历史价格涨跌幅 |

---

## 快速开始

### 环境要求

- Python 3.12+
- `bnbagent-studio` CLI：`uv tool install bnbagent-studio`
- BSC 测试网钱包（需要 tBNB 支付 Gas + U token 支付服务费）
- Kimi API Key（在 [platform.moonshot.cn](https://platform.moonshot.cn) 获取）

### 配置

**LLM（Kimi）** — 在 `app/agent/studio.toml` 中配置：

```toml
[llm]
provider = "openai"
model    = "moonshot-v1-32k"
base_url = "https://api.moonshot.cn/v1"
```

将 API Key 写入 `.studio/.env.local`（此文件不提交到代码库）：

```bash
OPENAI_API_KEY=sk-...your-kimi-key...
WALLET_PASSWORD=your-keystore-password
```

**存储** — 可交付物写入本地磁盘（`studio.toml` 中 `kind = "local"`）。链上存储的 URL 指向本地路径；生产环境请配置 IPFS Pinning 服务。

### 启动 Agent

```bash
# 在 stockanalyst/ 目录下执行
app/agent/.venv/bin/bag dev        # A2A 服务器运行在 http://localhost:9000
```

### 快速测试（仅报价）

```bash
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "test-01",
        "parts": [{"kind": "data", "data": {
          "skill": "negotiate",
          "task_description": "分析 AAPL、NVDA",
          "terms": {
            "deliverables": "Markdown 格式股票分析报告",
            "quality_standards": "真实行情数据，包含 RSI、MACD 和基本面分析"
          }
        }}]
      }
    }
  }'
```

> Agent 使用 A2A JSON-RPC 协议，接口为 `/`（非 REST `/negotiate`）。请求数据部分必须包含 `skill` 字段。

---

## E2E 测试流程

完整的 ERC-8183 买家流程共 6–7 步：

```
1. negotiate         → 签名报价（1 U）
2. createJob         → 链上创建 Job
3. registerJob       → 绑定 OptimisticPolicy
4. setBudget         → 设置托管金额
5. approve + fund    → U token 存入托管合约
6. notify_funded     → 通知 Agent 开始 LLM 分析
7. poll + fetch      → 等待 SUBMITTED，读取报告
8. settle            → 释放托管款项给卖家
```

提供两种买家实现。测试时两者均使用与卖家**相同的钱包**进行自测。

### BSC 测试网合约地址

| 合约 | 地址 |
|------|------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **MegaFuel Paymaster：** BSC 测试网公共 Paymaster 接受交易但永远不会确认。两个客户端均禁用了 Paymaster，改为直接自付 Gas（约 0.1 gwei）。

---

### 方案 A — Python 买家（`test_e2e.py`）

直接使用 `bnbagent_studio_core` 买家 API。

**前置条件：** `bag dev` 在另一终端运行，钱包已充值 tBNB + U token。

```bash
# 在 stockanalyst/ 目录下执行
app/agent/.venv/bin/python test_e2e.py
```

**执行步骤：**

```
Step 1: A2A negotiate         → price=1 U，报价已签名
Step 2: buy_workflow()        → createJob → registerJob → setBudget → approve → fund
Step 3: notify_funded（A2A） → Agent ACK "delivery started"
Step 4: poll getJob()         → FUNDED → SUBMITTED
Step 5: fetch_workflow()      → 从链上读取可交付物 URL，下载报告
Step 6: settle_workflow()     → router.settle()，托管款项释放
```

脚本通过 monkey-patch 禁用 MegaFuel Paymaster，所有交易自付 Gas。negotiate 和 notify_funded 直接调用 Agent 的 A2A 端点（SDK 的 `negotiate_with_seller` 针对 REST `/negotiate`，而本 Agent 服务 JSON-RPC at `/`）。

---

### 方案 B — TypeScript UOMP 买家（`buyer-client/`）

集成 UOMP（用户自主内存协议）上下文层的独立 TypeScript 客户端。位于本目录的 `../buyer-client/`。

**UOMP 的作用：** 在报价协商前，客户端从本地内存加载持仓信息（`portfolio:holdings` 标签）和风险偏好（`profile:risk` 标签），自动生成个性化任务描述。

#### 安装

```bash
cd ../buyer-client
npm install
```

配置 `.env`：

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67.json
WALLET_PASSWORD=your-keystore-password
AGENT_ENDPOINT=http://localhost:9000
PROVIDER_ADDRESS=0x1FF095E1C5Cf4bC72a3DC54be17B6cf85043Fb67
```

#### 运行

```bash
# 在 buyer-client/ 目录，保持 stockanalyst/ 的 bag dev 运行
npm run dev
```

**执行步骤（7 步）：**

```
Step 1: 加载 UOMP 上下文    → AAPL/NVDA 持仓 + 稳健型风险偏好
Step 2: negotiate（A2A）     → price=1 U，签名报价
Step 3: 链上买入             → createJob → registerJob → setBudget → approve → fund
Step 4: notify_funded（A2A） → Agent ACK status=accepted
Step 5: poll getJob()        → FUNDED → SUBMITTED
Step 6: 获取可交付物         → 从 Policy.JobInitialised 事件读取 URL，下载报告
Step 7: settle               → router.settle()，托管款项释放
```

客户端从加密 keystore 加载钱包（与 Agent 共用同一文件），使用 ethers.js v6 进行所有链上操作，直接从交易回执解码 `JobCreated` 事件（无需 `eth_getLogs` 调用，避免 BSC 测试网速率限制）。

#### UOMP 上下文

```typescript
// src/uomp.ts — 镜像 @uomp/sdk 接口
// 预设持仓：AAPL（50 股，稳健型）+ NVDA（30 股，成长型）
// 风险偏好：稳健型 — 单仓最大 15%，侧重基本面 + 技术面

// 生产环境替换为：
// import { UserMemory } from "@uomp/sdk";
// const memory = new UserMemory({ token: UOM_TOKEN });
```

---

## 项目结构

```
stockanalyst/
├── app/agent/
│   ├── analysis.py        # 股票数据引擎 — yfinance + RSI/MACD/布林带
│   ├── tools.py           # LLM 只读工具（get_stock_quote, get_technical_signals）
│   ├── seller_core.py     # ERC-8183 卖家逻辑 — negotiate / notify_funded / fulfill
│   ├── signing.py         # 确定性签名 — 报价签名 / 提交 / 结算
│   ├── main.py            # A2A 入口，监听 0.0.0.0:9000
│   ├── executor.py        # A2A 协议层（SellerAgentExecutor）
│   ├── managed_model.py   # LLM 适配器（当前使用 Kimi，OpenAI 兼容 API）
│   ├── agent_card.py      # A2A Agent Card 构建器
│   ├── studio.toml        # Agent 配置（钱包、LLM、定价、存储）
│   └── pyproject.toml     # 依赖声明
├── test_e2e.py            # Python E2E 买家测试
└── .studio/               # 钱包 Keystore — 永远不提交

../buyer-client/
├── src/
│   ├── index.ts           # 主 E2E 运行器（7 步）
│   ├── uomp.ts            # UOMP 上下文层（持仓 + 风险偏好）
│   ├── negotiate.ts       # A2A 报价 + buildJobDescription + notifyFunded
│   ├── erc8183.ts         # 链上买家操作（ethers.js v6）
│   └── abi/               # 合约 ABI（commerce, router, policy, erc20）
└── package.json
```

---

## 定价

| 分析数量 | 价格 |
|----------|------|
| 任意股票数 | **1.0 U**（测试网） |
| 价格区间 | 0.5 U – 5.0 U |

货币：BSC 测试网 `$U` token（`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`）

---

## 单元测试

```bash
cd stockanalyst

# 分析引擎 — 从 yfinance 拉取真实行情数据
app/agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'app/agent')
from analysis import fetch_quote, fetch_technical_signals
q = fetch_quote('AAPL')
print(f'价格: {q[\"price\"]}  PE: {q[\"pe_ratio\"]}  板块: {q[\"sector\"]}')
t = fetch_technical_signals('AAPL')
print(f'RSI-14: {t[\"rsi_14\"]}  MACD: {t[\"macd\"][\"crossover\"]}')
"
```

## CI 自动测试

[CI Workflow](.github/workflows/ci.yml) 在每次 Push / PR 时自动运行：
- 代码检查：`ruff check` — 零容忍
- 分析引擎：拉取真实 AAPL 数据，验证 RSI/MACD/布林带
- 工具加载：所有 LLM 工具注册正确
- 提示词构建器：结构化提示词生成验证

---

## 部署（托管平台 — 48h 测试沙盒）

```bash
bag platform login          # GitHub 设备流认证
bag deploy agent            # 构建 arm64 镜像并部署
bag deploy status           # 查看部署状态
```
