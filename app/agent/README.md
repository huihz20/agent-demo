# app/agent — Layer A (Agent / Sole Signer)

The valuable agent and **sole key-holder/signer** for the stockanalyst seller. See the [project README](../../README.md) for full documentation.

## Key files

| File | Purpose |
|------|---------|
| `analysis.py` | yfinance data engine + RSI/MACD/Bollinger computation |
| `tools.py` | LLM-callable read-only tools (`get_stock_quote`, `get_technical_signals`) |
| `seller_core.py` | ERC-8183 seller logic — negotiate / notify_funded / fulfill |
| `signing.py` | Deterministic signing — quote / submit / settle (never LLM tools) |
| `main.py` | A2A entrypoint on `0.0.0.0:9000` |
| `studio.toml` | Agent config (wallet, LLM, pricing, storage) |

## Run locally

```bash
# From workspace root
app/agent/.venv/bin/bag dev    # A2A server on http://localhost:9000
```
