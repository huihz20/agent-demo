/**
 * Minimal UOMP Memory Guard mock — no dependencies, plain Node.js.
 * Serves portfolio:holdings + profile:risk for the buyer client.
 *
 * Usage: node guard-mock.mjs
 */
import { createServer } from "http";

const TOKEN = "demo-guard-token";
const PORT = 9374;
const now = new Date().toISOString();

const ITEMS = [
  {
    key: "portfolio:AAPL",
    value: { symbol: "AAPL", shares: 50, avgCost: 170.0, currency: "USD" },
    tags: ["portfolio:holdings", "symbol:AAPL"],
    sensitivity: "medium", source: "user",
    createdAt: now, updatedAt: now,
    description: "AAPL position",
  },
  {
    key: "portfolio:NVDA",
    value: { symbol: "NVDA", shares: 20, avgCost: 480.0, currency: "USD" },
    tags: ["portfolio:holdings", "symbol:NVDA"],
    sensitivity: "medium", source: "user",
    createdAt: now, updatedAt: now,
    description: "NVDA position",
  },
  {
    key: "profile:risk",
    value: {
      tolerance: "moderate",
      horizonMonths: 12,
      preferredIndicators: ["RSI-14", "MACD", "Bollinger Bands"],
    },
    tags: ["profile:risk", "preferences"],
    sensitivity: "low", source: "user",
    createdAt: now, updatedAt: now,
    description: "User risk profile",
  },
];

const server = createServer((req, res) => {
  const auth = req.headers["authorization"] ?? "";
  if (auth !== `Bearer ${TOKEN}`) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Unauthorized" }));
    return;
  }

  const url = new URL(req.url, "http://localhost");

  if (req.method === "GET" && url.pathname === "/v1/memory") {
    const tag = url.searchParams.get("tag") ?? "";
    const items = ITEMS.filter((i) => i.tags.includes(tag));
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ items }));
    return;
  }

  const m = url.pathname.match(/^\/v1\/memory\/(.+)$/);
  if (req.method === "GET" && m) {
    const key = decodeURIComponent(m[1]);
    const item = ITEMS.find((i) => i.key === key) ?? null;
    if (!item) {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Not found" }));
      return;
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(item));
    return;
  }

  res.writeHead(404);
  res.end();
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`UOMP Guard mock  →  http://127.0.0.1:${PORT}`);
  console.log(`Token: ${TOKEN}`);
  console.log("Portfolio: AAPL ×50 @ $170  |  NVDA ×20 @ $480");
  console.log("Risk:      moderate / 12mo / RSI-14 MACD Bollinger\n");
});
