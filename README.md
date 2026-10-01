# Steam Scout MCP

**Steam market research for AI assistants — regional prices, reviews, player counts, charts and news. No API key required.**

An [MCP](https://modelcontextprotocol.io) server that lets Claude, Cursor, VS Code and other MCP clients answer questions like:

- *"How much does Hades II cost in the US, Germany, Japan, Korea and China — which region is cheapest in USD?"*
- *"Compare Stardew Valley and Terraria: price, review score and how many people are playing right now."*
- *"What's in the Japanese Steam top sellers today?"*
- *"Summarize the latest patch notes and recent Korean reviews for this game."*

> 日本語: Steam の地域別価格・レビュー・同時接続数・ランキングを AI アシスタントから調べられる MCP サーバー（API キー不要）。
> 한국어: Steam 지역별 가격, 리뷰, 동시 접속자 수, 차트를 AI 어시스턴트에서 조회하는 MCP 서버 (API 키 불필요).
> 中文：让 AI 助手查询 Steam 各地区价格、评测、在线人数和排行榜的 MCP 服务器（无需 API 密钥）。

## Features

| Tool | What it returns |
|---|---|
| `search_games` | Find games by name → `app_id`, price, platforms, Metascore |
| `game_details` | Store facts: description, developers, release date, price, genres, languages |
| `regional_prices` | One game's price across regions (default: US, UK, DE, FR, JP, KR, CN, TW, HK), converted to USD (or any currency) and sorted cheapest first, with the price spread |
| `review_summary` | Review score ("Very Positive"…), positive %, totals, recent reviews in any language |
| `player_count` | Players in the game right now |
| `compare_games` | 2–8 games side by side: price, genres, review score, current players |
| `store_charts` | Top sellers, new releases, specials or coming soon — per region |
| `game_news` | Latest patch notes and announcements |

- **Zero setup**: only public Steam endpoints — no Steam account or API key.
- **Currency conversion** with European Central Bank reference rates.
- **Polite by design**: responses cached for 5 minutes, limited concurrency, retries with back-off.
- All tools are read-only.

## Installation

Requires Python 3.10+. The easiest way is [uv](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/<your-github-username>/steam-scout-mcp steam-scout-mcp --version
```

Or with pip:

```bash
pip install git+https://github.com/<your-github-username>/steam-scout-mcp
```

## Usage

### Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "steam-scout": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<your-github-username>/steam-scout-mcp", "steam-scout-mcp"]
    }
  }
}
```

A ready-to-copy file is in [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json).

### Claude Code

```bash
claude mcp add steam-scout -- uvx --from git+https://github.com/<your-github-username>/steam-scout-mcp steam-scout-mcp
```

### Remote (HTTP)

```bash
steam-scout-mcp --transport streamable-http
```

## Project structure

```
src/steam_scout_mcp/
├── server.py   # MCP tools, input validation, CLI entry point
└── steam.py    # async client for public Steam endpoints (cache, retries, FX rates)
tests/
└── test_server.py  # offline tests with mocked Steam + in-process MCP client
examples/
└── claude_desktop_config.json
```

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip install -e ".[dev]"
.venv/bin/pytest
```

Tests never touch the network: Steam and the exchange-rate service are replaced by `httpx.MockTransport`.

## Notes

- Prices come from the Steam store for each region and may differ from checkout prices (taxes, local promotions).
- Some regions have no ECB exchange rate (e.g. TWD); those prices are listed without conversion.
- This project is not affiliated with Valve. Steam is a trademark of Valve Corporation.

## License

MIT
