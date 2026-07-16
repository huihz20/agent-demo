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

A professional stock analysis agent deployed on [BNBChain AI Studio](https://www.bnbchain.org), built with the ERC-8183 commerce protocol. Buyers submit a list of stock symbols and receive a comprehensive Markdown report — valuation, technical signals, and risk ratings — backed by real market data fetched via [yfinance](https://github.com/ranaroussi/yfinance).

### How it works

```
Buyer → negotiate (signed quote: 1.0 U) → fund on-chain
      → notify_funded → Agent fetches live data + runs LLM
      → Markdown report pinned to IPFS → result submitted on-chain
      → Buyer reads deliverable from chain
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

### Local Development

```bash
# From workspace root (stockanalyst/)
app/agent/.venv/bin/bag dev        # A2A server on http://localhost:9000
```

Test the negotiate skill:

```bash
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "test-01",
        "parts": [{"kind": "data", "data": {
          "skill": "negotiate",
          "task_description": "Analyze AAPL, NVDA, TSLA",
          "terms": {
            "deliverables": "Stock analysis report in Markdown",
            "quality_standards": "Real market data, RSI, MACD, fundamental analysis"
          }
        }}]
      }
    }
  }'
```

### Calling the Agent (buyer side)

```python
# terms payload accepted by notify_funded
{
  "symbols": ["AAPL", "NVDA", "TSLA"],   # required
  "analysis_type": "comprehensive",       # fundamental | technical | comprehensive
  "language": "en"                        # en | zh
}
```

---

## Testing

### Unit tests (no server needed)

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

### Negotiate test (requires running agent)

```bash
# Terminal 1
app/agent/.venv/bin/bag dev

# Terminal 2 — get a signed price quote
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {"message": {"role": "user", "messageId": "t1",
      "parts": [{"kind": "data", "data": {
        "skill": "negotiate",
        "task_description": "Analyze AAPL and NVDA",
        "terms": {
          "deliverables": "Comprehensive stock analysis report in Markdown",
          "quality_standards": "Real market data, RSI-14, MACD, Bollinger Bands"
        }
      }}]}}}'
```

> **Note:** The agent uses A2A JSON-RPC at `/` — not a REST `/negotiate` endpoint.
> The `skill` field is required in the data part.

### Full E2E test (on-chain)

`test_e2e.py` runs the complete buyer flow: negotiate → fund on-chain → notify agent → poll → fetch report → settle.

**Prerequisites:**
- `bag dev` running in another terminal
- Wallet funded with tBNB (gas) and U tokens on BSC testnet
- Pieverse LLM credits available (check with `app/agent/.venv/bin/bag llm balance`)

```bash
# Run from stockanalyst/
app/agent/.venv/bin/python test_e2e.py
```

Expected output:
```
Step 1: Negotiate       ✓ price=1 U, quote signed
Step 2: Buy (on-chain)  ✓ Job #N created, fund_tx confirmed
Step 3: notify_funded   ✓ Agent ACK "delivery started"
Step 4: Poll status     ... FUNDED → SUBMITTED
Step 5: Fetch report    --- REPORT (Markdown) ---
Step 6: Settle          ✓ approve tx confirmed
```

**Tip — if the LLM hits a daily rate limit** (`auto/free` cap):

```bash
app/agent/.venv/bin/bag llm topup --amount 1   # charge 1 U from wallet to Pieverse
```

Then re-run the test. The `auto_renew` hook handles allocation automatically.

> **BSC testnet note:** `test_e2e.py` disables the MegaFuel paymaster, which is
> unreliable on testnet. Transactions are broadcast directly (self-pay at ~0.1 gwei).

### CI (automated on every push/PR)

The [CI workflow](.github/workflows/ci.yml) runs automatically on GitHub Actions:
- Lint: `ruff check` — zero tolerance
- Analysis engine: fetches real AAPL data, asserts RSI/MACD/Bollinger are present
- Tools load: all LLM tools registered correctly
- Prompt builder: structured prompt generation verified

---

## Project Structure

```
app/agent/
├── analysis.py        # Stock data engine — yfinance + RSI/MACD/Bollinger
├── tools.py           # LLM-callable read-only tools (get_stock_quote, get_technical_signals)
├── seller_core.py     # ERC-8183 seller logic — negotiate / notify_funded / fulfill
├── signing.py         # Deterministic signing — quote-sign / submit / settle (never LLM tools)
├── main.py            # A2A entrypoint on 0.0.0.0:9000
├── executor.py        # A2A wire (SellerAgentExecutor)
├── managed_model.py   # Pieverse LLM adapter with credit auto-renew
├── agent_card.py      # A2A agent card builder
├── studio.toml        # Agent config (wallet, LLM, pricing, storage)
└── pyproject.toml     # Dependencies
```

---

## Pricing

| Symbols | Price |
|---------|-------|
| Any count | **1.0 U** (testnet) |
| Floor / Ceiling | 0.5 U – 5.0 U |

Currency: `$U` token on BSC testnet (`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`)

---

## Deploy (Managed Platform — 48h Testnet Trial)

```bash
bag platform login          # GitHub device flow
bag deploy agent            # build + push arm64 image → deploy
bag deploy status           # check deployment state
```

The platform injects secrets into the operator's Secrets Manager and routes to the agent's A2A surface. The trial is testnet-forced and auto-reclaimed at 48h.

---

## CI / CD

This repository uses **GitHub Actions** for automated testing and deployment.

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| [CI](.github/workflows/ci.yml) | Push / PR to `main` | Lint (`ruff`) + analysis engine tests + tools load test |
| [Deploy](.github/workflows/deploy.yml) | Push to `main` (requires `production` environment approval) | `bag deploy agent` to BNB Chain platform |

### Required GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|--------|-------------|
| `WALLET_PASSWORD` | Keystore encryption password |
| `PIEVERSE_LLM_API_KEY` | Pieverse LLM API key (written by `bag llm activate`) |
| `STORAGE_API_URL` | IPFS pinning service upload endpoint |
| `STORAGE_API_KEY` | IPFS pinning service write key |
| `BAG_PLATFORM_TOKEN` | `bag platform login` session token (deploy only) |

---

<a name="中文"></a>

---

# 中文

## 概述

基于 [BNBChain AI Studio](https://www.bnbchain.org) 构建的专业股票分析 Agent，采用 ERC-8183 商业协议。买家提交股票代码列表，Agent 拉取真实行情数据，运行 LLM 深度分析，生成包含估值、技术信号、风险评级的完整 Markdown 报告，结果固定到 IPFS 并写入链上。

### 工作流程

```
买家 → negotiate（签名报价：1.0 U）→ 链上打款
     → notify_funded → Agent 拉取实时数据 + LLM 分析
     → Markdown 报告固定至 IPFS → 链上提交结果
     → 买家从链上读取可交付物
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

### 本地开发

```bash
# 在工作区根目录（stockanalyst/）执行
app/agent/.venv/bin/bag dev        # A2A 服务器运行在 http://localhost:9000
```

测试报价功能：

```bash
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "test-01",
        "parts": [{"kind": "data", "data": {
          "skill": "negotiate",
          "task_description": "分析 AAPL、NVDA、TSLA",
          "terms": {
            "deliverables": "Markdown 格式股票分析报告",
            "quality_standards": "真实行情数据，包含 RSI、MACD 和基本面分析"
          }
        }}]
      }
    }
  }'
```

### 买家调用参数

```python
# notify_funded 接受的 terms 格式
{
  "symbols": ["AAPL", "NVDA", "TSLA"],   # 必填：股票代码列表
  "analysis_type": "comprehensive",       # fundamental | technical | comprehensive
  "language": "zh"                        # en | zh
}
```

---

## 测试

### 单元测试（无需启动服务）

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

### 报价测试（需要先启动 Agent）

```bash
# 终端 1
app/agent/.venv/bin/bag dev

# 终端 2 — 获取签名报价
curl -s -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {"message": {"role": "user", "messageId": "t1",
      "parts": [{"kind": "data", "data": {
        "skill": "negotiate",
        "task_description": "分析 AAPL 和 NVDA",
        "terms": {
          "deliverables": "Markdown 格式股票分析报告",
          "quality_standards": "真实行情数据，包含 RSI-14、MACD、布林带"
        }
      }}]}}}'
```

> **注意：** Agent 使用 A2A JSON-RPC 协议，接口为 `/`（非 REST `/negotiate`）。
> 请求的数据部分必须包含 `skill` 字段。

### 完整链上 E2E 测试

`test_e2e.py` 覆盖完整买家流程：报价协商 → 链上资助 → 通知 Agent → 轮询 → 获取报告 → 结算。

**前置条件：**
- 另一个终端中已运行 `bag dev`
- 钱包已充值 tBNB（Gas）和 BSC 测试网 U token
- Pieverse LLM 有可用余额（用 `app/agent/.venv/bin/bag llm balance` 检查）

```bash
# 在 stockanalyst/ 目录下运行
app/agent/.venv/bin/python test_e2e.py
```

预期输出：
```
Step 1: Negotiate       ✓ price=1 U，报价已签名
Step 2: Buy (链上)      ✓ Job #N 创建并资助成功，fund_tx 已确认
Step 3: notify_funded   ✓ Agent ACK "delivery started"
Step 4: 轮询状态        ... FUNDED → SUBMITTED
Step 5: 获取报告        --- 完整 Markdown 分析报告 ---
Step 6: 结算            ✓ approve tx 已确认
```

**LLM 达到每日免费限额时（`auto/free` 每日上限）：**

```bash
app/agent/.venv/bin/bag llm topup --amount 1   # 从钱包向 Pieverse 充值 1 U
```

充值后重新运行测试，`auto_renew` 钩子会自动完成分配。

> **BSC 测试网说明：** `test_e2e.py` 已禁用 MegaFuel Paymaster（测试网上不稳定），
> 改用直接广播（自付 Gas，约 0.1 gwei）。

### CI 自动测试

[CI Workflow](.github/workflows/ci.yml) 在每次 Push / PR 时自动运行：
- 代码检查：`ruff check` — 零容忍
- 分析引擎：拉取真实 AAPL 数据，验证 RSI/MACD/布林带
- 工具加载：所有 LLM 工具注册正确
- 提示词构建器：结构化提示词生成验证

---

## 项目结构

```
app/agent/
├── analysis.py        # 股票数据引擎 — yfinance + RSI/MACD/布林带计算
├── tools.py           # LLM 只读工具（get_stock_quote, get_technical_signals）
├── seller_core.py     # ERC-8183 卖家逻辑 — negotiate / notify_funded / fulfill
├── signing.py         # 确定性签名 — 报价签名 / 提交 / 结算（非 LLM 工具）
├── main.py            # A2A 入口，监听 0.0.0.0:9000
├── executor.py        # A2A 协议层（SellerAgentExecutor）
├── managed_model.py   # Pieverse LLM 适配器（含自动充值）
├── agent_card.py      # A2A Agent Card 构建器
├── studio.toml        # Agent 配置（钱包、LLM、定价、存储）
└── pyproject.toml     # 依赖声明
```

---

## 定价

| 分析数量 | 价格 |
|----------|------|
| 任意股票数 | **1.0 U**（测试网） |
| 价格区间 | 0.5 U – 5.0 U |

货币：BSC 测试网 `$U` token（`0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565`）

---

## 部署（托管平台 — 48h 测试沙盒）

```bash
bag platform login          # GitHub 设备流认证
bag deploy agent            # 构建 arm64 镜像并部署
bag deploy status           # 查看部署状态
```

平台会将密钥注入运营商的 Secrets Manager，并路由到 Agent 的 A2A 接口。测试沙盒强制使用测试网，48 小时后自动回收。

---

## CI/CD 流水线

本仓库使用 **GitHub Actions** 实现自动化测试与部署。

| Workflow | 触发条件 | 操作 |
|----------|----------|------|
| [CI](.github/workflows/ci.yml) | Push / PR 到 `main` | 代码检查 + 分析引擎测试 + 工具加载测试 |
| [Deploy](.github/workflows/deploy.yml) | Push 到 `main`（需 `production` 环境审批） | `bag deploy agent` 部署到 BNB Chain 平台 |

### GitHub Secrets 配置

进入 **Settings → Secrets and variables → Actions** 添加：

| Secret | 说明 |
|--------|------|
| `WALLET_PASSWORD` | 钱包密钥库加密密码 |
| `PIEVERSE_LLM_API_KEY` | Pieverse LLM API 密钥（由 `bag llm activate` 写入） |
| `STORAGE_API_URL` | IPFS Pinning 服务上传地址 |
| `STORAGE_API_KEY` | IPFS Pinning 服务写入密钥 |
| `BAG_PLATFORM_TOKEN` | `bag platform login` 会话令牌（仅部署需要） |
