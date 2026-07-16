# Stock Analysis Agent

[![ERC-8183](https://img.shields.io/badge/Protocol-ERC--8183-blue)](https://github.com/bnb-chain/BEPs)
[![UOMP](https://img.shields.io/badge/Context-UOMP-purple)](https://github.com/0xaicrypto/uomp-core)
[![Network](https://img.shields.io/badge/Network-BSC%20Testnet-yellow)](https://testnet.bscscan.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org)

> [中文](#中文) | English

---

## 1. How It Works

### Two Protocols, One Flow

| Protocol | Role | What it handles |
|----------|------|-----------------|
| **ERC-8183** | Commerce | On-chain job creation, escrow, payment settlement |
| **UOMP** | User context + delivery | Portfolio data, deliverable relay |

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
  │                               │◄── submit_result(report_url)  │
  │                               │                               │
  ├── poll getJob() ─────────────►│                               │
  │◄─ SUBMITTED ─────────────────│                               │
  │                               │                               │
  ├── fetch report via UOMP relay ────────────────────────────── │
  │◄─ Markdown report ────────────────────────────────────────── │
  │                               │                               │
  └── settle ────────────────────►│ escrow released to seller     │
```

### UOMP: Personalized Context + Reverse Delivery

UOMP solves two problems ERC-8183 alone cannot:

**User context**: The buyer's portfolio and risk preferences are stored in their own Memory Guard. Before negotiating, the buyer reads this data and builds a personalized task description. The agent never receives raw personal data.

**Reverse delivery**: The seller runs in the cloud with no public URL it can push to. The buyer starts a local relay on `:9444`, exposes it via Cloudflare Tunnel, and passes the tunnel URL in `notify_funded`. The seller uploads the report there; the buyer fetches it from its own relay.

---

## 2. Architecture

### System Overview

```
┌─────────────────── LOCAL (buyer machine) ───────────────────────────────┐
│                                                                           │
│  UOMP Guard mock (localhost:9374)                                         │
│   portfolio:holdings                                                      │
│   profile:risk          ──[1. read]──►  buyer-client (Node.js)           │
│                                                 │                         │
│                         ┌───────────────────────┼────────────────┐       │
│                         │                       │                │       │
│                    [2. negotiate]        [3. on-chain ops]  [relay setup] │
│                    OAuth2 token          createJob           localhost:9444│
│                         │               registerJob          Cloudflare   │
│                         │               setBudget            Tunnel ──────┼──┐
│                         │               approve                           │  │
│                         │               fund ────────────────────────────┼──┼──►BSC Testnet
│                         │                                                 │  │
└─────────────────────────┼─────────────────────────────────────────────────┘  │
                          │ [4. notify_funded]                                   │
                          │   + tunnel URL + token                               │
                          ▼                                                       │
┌─────────────────────────────────────────────────────────────────────────────┐  │
│              BNB Chain Platform (cloud seller)                               │  │
│                                                                              │  │
│   A2A endpoint ◄──────────────────────────────[2. negotiate]────────────────┤  │
│                ◄──────────────────────────────[4. notify_funded]             │  │
│                                                                              │  │
│   background work:                                                           │  │
│     read UOMP context (AAPL, NVDA, risk=moderate)                           │  │
│     LLM analysis (Kimi moonshot-v1-32k)                                     │  │
│     submit_result ──────────────────────────────────────────────────────────┼──┼──►BSC Testnet
│     POST report ────────────────────────────────────────────────────────────┼──┘
│       → Cloudflare Tunnel → buyer relay → stored locally                    │
└──────────────────────────────────────────────────────────────────────────────┘

[5. poll chain] ──► BSC Testnet ──► SUBMITTED
[6. fetch report] ──► tunnel URL → buyer relay → Markdown report
[7. settle] ──► BSC Testnet ──► escrow released to seller
```

### BSC Testnet Contract Addresses

| Contract | Address |
|----------|---------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token (ERC-20) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **MegaFuel Paymaster**: The BSC testnet public paymaster accepts transactions but never confirms them. The buyer client disables it; transactions pay gas directly (~3 gwei).

---

## 3. E2E Testing

### TypeScript UOMP Buyer (`buyer-client/`)

Cloud seller deployed on BNB Chain platform + local buyer with UOMP user context and Cloudflare Tunnel reverse gateway.

#### Prerequisites

- Node.js 18+, [cloudflared](https://github.com/cloudflare/cloudflared) installed
- Buyer wallet funded: ≥ 0.01 tBNB (gas) + ≥ 1.0 U (escrow)
- Seller already deployed on BNB Chain platform (see below)

#### Deploy the seller

```bash
cd stockanalyst/app/agent

# Single-quote the password — prevents shell expansion of special characters
# (e.g. $r in a password would otherwise be silently stripped by bash)
export WALLET_PASSWORD='<your-keystore-password>'

bag deploy agent
```

After deploy, `studio.toml` records `[deploy.platform]` with `agent_id` and `invoke_url`. Create an OAuth2 client from the platform console to get `client_id` and `client_secret`.

#### Install buyer dependencies

```bash
# macOS
brew install cloudflare/cloudflare/cloudflared

cd buyer-client
npm install
```

#### Configure `buyer-client/.env`

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/<address>.json
WALLET_PASSWORD=<your-keystore-password>
AGENT_ENDPOINT=https://bnbagent-api.bnbchain.world/v1/rt/<agent_id>/a2a
AGENT_CLIENT_ID=<client_id from platform console>
AGENT_CLIENT_SECRET=<client_secret from platform console>
PROVIDER_ADDRESS=<seller wallet address>
UOMP_GUARD_URL=http://127.0.0.1:9374
UOMP_GUARD_TOKEN=demo-guard-token
```

#### Run

**Terminal 1 — UOMP Guard mock** (serves portfolio + risk profile):

```bash
cd agent-demo   # repo root
node guard-mock.mjs
```

**Terminal 2 — buyer client:**

```bash
cd buyer-client
npm run dev
```

**Steps:**

| Step | Action | Detail |
|------|--------|--------|
| 1 | Load UOMP context | Guard → AAPL ×50, NVDA ×20, risk=moderate |
| 2 | `negotiate` | OAuth2 token fetch → A2A → signed quote 1.0 U |
| 3 | On-chain buy | createJob → registerJob → setBudget → approve → fund |
| 4 | `notify_funded` | Pass Cloudflare Tunnel URL + token to seller → ACK |
| 5 | Poll chain | `getJob()` every 15s; FUNDED → SUBMITTED (~30s) |
| 6 | Fetch report | Tunnel URL → local relay → report displayed inline |
| 7 | Settle | After 24h dispute window: `bag erc8183 settle <job_id>` |

---

## Pricing

| Stocks | Price |
|--------|-------|
| Any count | **1.0 U** (testnet) |
| Floor / Ceiling | 0.5 U – 5.0 U |

Currency: U token on BSC testnet (`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`)

---

## BSC Testnet Resources

| Resource | Link |
|----------|------|
| tBNB faucet (gas) | https://testnet.bnbchain.org/faucet-smart |
| U token faucet (escrow) | https://united-coin-u.github.io/u-faucet/ |
| BSC testnet explorer | https://testnet.bscscan.com |

---

## CI

[CI Workflow](.github/workflows/ci.yml) runs on every push:
- `ruff check` — zero tolerance
- yfinance real data test (AAPL RSI / MACD / Bollinger)
- LLM tool registration check

---

<a name="中文"></a>

# 中文

## 1. 原理介绍

### 两个协议，一个流程

| 协议 | 角色 | 负责内容 |
|------|------|---------|
| **ERC-8183** | 商业协议 | 链上 Job 创建、资金托管、付款结算 |
| **UOMP** | 用户上下文 + 交付 | 持仓数据、报告反向传递 |

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
  │                               │◄ submit_result(report_url)   │
  │                               │                               │
  ├── 轮询 getJob()──────────────►│                               │
  │◄─ SUBMITTED ─────────────────│                               │
  │                               │                               │
  ├── 通过 UOMP 中继获取报告 ──────────────────────────────────── │
  │◄─ Markdown 报告 ──────────────────────────────────────────── │
  │                               │                               │
  └── settle ────────────────────►│  托管款释放给卖家              │
```

### UOMP：个性化上下文 + 反向交付

UOMP 解决了 ERC-8183 单独无法解决的两个问题：

**用户上下文**：买家的持仓和风险偏好存放在自己的 Memory Guard 中。协商前，买家读取这些数据并构建个性化任务描述，Agent 不直接接收原始个人数据。

**反向交付**：卖家在云端运行，没有公网 IP 可供买家主动拉取报告。买家在本地启动 `:9444` 中继，通过 Cloudflare Tunnel 暴露，并在 `notify_funded` 时把 Tunnel URL 传给卖家。卖家把报告上传到那里，买家从自己的本地中继读取。

---

## 2. 架构设计

### 系统总览

```
┌─────────────────── 本地（买家机器）──────────────────────────────────────┐
│                                                                           │
│  UOMP Guard mock（localhost:9374）                                        │
│   portfolio:holdings                                                      │
│   profile:risk          ──[1. 读取]──►  buyer-client（Node.js）          │
│                                                 │                         │
│                         ┌───────────────────────┼────────────────┐       │
│                         │                       │                │       │
│                    [2. 协商]            [3. 链上操作]       [中继启动]    │
│                    OAuth2 Token         createJob          localhost:9444 │
│                         │               registerJob        Cloudflare    │
│                         │               setBudget          Tunnel ───────┼──┐
│                         │               approve                          │  │
│                         │               fund ───────────────────────────┼──┼──►BSC 测试网
│                         │                                                │  │
└─────────────────────────┼──────────────────────────────────────────────┘   │
                          │ [4. notify_funded]                                 │
                          │   + Tunnel URL + Token                              │
                          ▼                                                      │
┌──────────────────────────────────────────────────────────────────────────┐   │
│              BNB Chain 平台（云端卖家）                                    │   │
│                                                                           │   │
│   A2A 端点 ◄────────────────────────────────[2. 协商]────────────────────┤   │
│            ◄────────────────────────────────[4. notify_funded]            │   │
│                                                                           │   │
│   后台执行：                                                               │   │
│     读取 UOMP 上下文（AAPL、NVDA，risk=moderate）                        │   │
│     LLM 分析（Kimi moonshot-v1-32k）                                     │   │
│     submit_result ────────────────────────────────────────────────────────┼───┼──►BSC 测试网
│     POST 报告 ─────────────────────────────────────────────────────────── ┼───┘
│       → Cloudflare Tunnel → 买家本地中继 → 存储到本地                     │
└───────────────────────────────────────────────────────────────────────────┘

[5. 轮询链上状态] ──► BSC 测试网 ──► SUBMITTED
[6. 获取报告] ──► Tunnel URL → 买家中继 → Markdown 报告
[7. 结算] ──► BSC 测试网 ──► 托管款释放给卖家
```

### BSC 测试网合约地址

| 合约 | 地址 |
|------|------|
| AgenticCommerce | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| U Token（ERC-20） | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

> **MegaFuel Paymaster**：BSC 测试网公共 paymaster 接受交易但不确认。买家客户端已禁用，直接支付 Gas（约 3 gwei）。

---

## 3. 端到端测试

### TypeScript UOMP 买家（`buyer-client/`）

云端 seller（BNB Chain 平台部署）+ 本地 buyer（UOMP 上下文 + Cloudflare Tunnel 反向网关）。

#### 前置条件

- Node.js 18+，已安装 [cloudflared](https://github.com/cloudflare/cloudflared)
- 买家钱包：≥ 0.01 tBNB（Gas）+ ≥ 1.0 U（托管）
- 卖家已部署到 BNB Chain 平台（见下文）

#### 部署卖家

```bash
cd stockanalyst/app/agent

# 必须用单引号 — 防止 bash 展开密码中的特殊字符
# （例如密码含 $r，bash source 会把 $r 静默展开为空字符串）
export WALLET_PASSWORD='<你的 keystore 密码>'

bag deploy agent
```

部署完成后，`studio.toml` 记录 `[deploy.platform]`，包含 `agent_id` 和 `invoke_url`。从平台控制台创建 OAuth2 客户端，获取 `client_id` 和 `client_secret`。

#### 安装买家依赖

```bash
# macOS
brew install cloudflare/cloudflare/cloudflared

cd buyer-client
npm install
```

#### 配置 `buyer-client/.env`

```env
KEYSTORE_PATH=../stockanalyst/.studio/wallets/<address>.json
WALLET_PASSWORD=<你的 keystore 密码>
AGENT_ENDPOINT=https://bnbagent-api.bnbchain.world/v1/rt/<agent_id>/a2a
AGENT_CLIENT_ID=<平台控制台的 client_id>
AGENT_CLIENT_SECRET=<平台控制台的 client_secret>
PROVIDER_ADDRESS=<卖家钱包地址>
UOMP_GUARD_URL=http://127.0.0.1:9374
UOMP_GUARD_TOKEN=demo-guard-token
```

#### 运行

**终端 1 — UOMP Guard mock**（提供持仓和风险偏好数据）：

```bash
cd agent-demo   # 仓库根目录
node guard-mock.mjs
```

**终端 2 — 买家客户端：**

```bash
cd buyer-client
npm run dev
```

**步骤：**

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 加载 UOMP 上下文 | Guard → AAPL ×50、NVDA ×20，risk=moderate |
| 2 | `negotiate`（A2A） | OAuth2 Token → A2A → 签名报价 1.0 U |
| 3 | 链上买入 | createJob → registerJob → setBudget → approve → fund |
| 4 | `notify_funded` | 传 Tunnel URL + Token 给卖家 → ACK |
| 5 | 轮询链上状态 | `getJob()` 每 15s；FUNDED → SUBMITTED（约 30s） |
| 6 | 获取报告 | Tunnel URL → 本地中继 → 报告内联显示 |
| 7 | 结算 | 24h 争议窗口后：`bag erc8183 settle <job_id>` |

---

## 定价

| 分析数量 | 价格 |
|----------|------|
| 任意股票数 | **1.0 U**（测试网） |
| 价格区间 | 0.5 U – 5.0 U |

货币：BSC 测试网 U token（`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`）

---

## 测试网资源

| 资源 | 链接 |
|------|------|
| tBNB 水龙头（Gas） | https://testnet.bnbchain.org/faucet-smart |
| U token 水龙头（托管） | https://united-coin-u.github.io/u-faucet/ |
| BSC 测试网浏览器 | https://testnet.bscscan.com |

---

## CI 自动测试

[CI Workflow](.github/workflows/ci.yml) 在每次 push 时运行：
- `ruff check` — 零容忍
- yfinance 真实数据测试（AAPL RSI/MACD/布林带）
- LLM 工具注册验证
