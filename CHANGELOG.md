# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-01

### Added
- Initial release of **Steam Scout MCP**, a Model Context Protocol server for Steam market research.
- Eight read-only MCP tools:
  - `search_games` — search the Steam store by name.
  - `game_details` — store page facts for a single app.
  - `regional_prices` — compare an app's price across regions with ECB currency conversion and cheapest-first sorting.
  - `review_summary` — review score, positive percentage, totals and recent review samples.
  - `player_count` — current concurrent players for a game.
  - `game_news` — latest patch notes and announcements.
  - `store_charts` — top sellers, new releases, specials or coming soon per region.
  - `compare_games` — side-by-side comparison of 2–8 games.
- Async Steam client (`SteamClient`) using public, keyless Steam Store and Steam Web API endpoints,
  with 5-minute response caching, concurrency limiting and retries with back-off.
- Currency conversion via the European Central Bank reference rates (frankfurter.dev).
- CLI entry point `steam-scout-mcp` supporting `stdio` and `streamable-http` transports.
- Example Claude Desktop configuration under `examples/`.
- Offline test suite using `httpx.MockTransport` and an in-process MCP client.

[Unreleased]: https://github.com/cyanspearfitting4/steam-scout-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cyanspearfitting4/steam-scout-mcp/releases/tag/v0.1.0
