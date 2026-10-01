"""MCP server exposing Steam market intelligence tools (no Steam API key required)."""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import re
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .steam import DEFAULT_REGIONS, STORE, SteamClient, SteamError, cents, clean_html

INSTRUCTIONS = """Tools for researching games on Steam: search, store details, regional prices
(US, Europe and East Asia by default) with conversion to one currency, review scores, current
player counts, store charts and news. Use search_games first to find an app_id when the user
gives a game name. All data comes from public Steam endpoints and is cached for five minutes."""

mcp = MCPServer(name="steam-scout", instructions=INSTRUCTIONS, version=__version__)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

_client: SteamClient | None = None


def client() -> SteamClient:
    global _client
    if _client is None:
        _client = SteamClient()
    return _client


# ---------------------------------------------------------------- validation helpers
def _country(code: str) -> str:
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError(f"country must be a two-letter ISO code such as US, DE, JP; got {code!r}")
    return code


def _app_id(app_id: int) -> int:
    if not isinstance(app_id, int) or app_id <= 0:
        raise ValueError("app_id must be a positive integer; use search_games to find it")
    return app_id


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _languages(raw: str | None) -> list[str]:
    """Steam lists languages as HTML with '*' marking full audio and a trailing footnote line."""
    text = clean_html(raw).split("\n")[0]
    return [x.strip(" *") for x in text.split(",") if x.strip(" *")][:40]


def _store_url(app_id: int) -> str:
    return f"{STORE}/app/{app_id}/"


def _review_block(summary: dict) -> dict:
    total = int(summary.get("total_reviews") or 0)
    positive = int(summary.get("total_positive") or 0)
    return {
        "score": summary.get("review_score_desc") or "No reviews",
        "positive_percent": round(100 * positive / total, 1) if total else None,
        "total_reviews": total,
        "positive": positive,
        "negative": int(summary.get("total_negative") or 0),
    }


# ---------------------------------------------------------------- tool logic (testable)
async def do_search_games(c: SteamClient, query: str, country: str = "US", limit: int = 10) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    country = _country(country)
    items = await c.search(query, country)
    results = []
    for it in items[:_clamp(limit, 1, 25)]:
        price = it.get("price") or {}
        results.append({
            "app_id": it.get("id"),
            "name": it.get("name"),
            "price": cents(price.get("final")) if price else 0.0,
            "currency": price.get("currency") if price else None,
            "platforms": [p for p, on in (it.get("platforms") or {}).items() if on],
            "metascore": int(it["metascore"]) if str(it.get("metascore", "")).isdigit() else None,
            "store_url": _store_url(it.get("id")),
        })
    return {"query": query, "country": country, "results": results}


async def do_game_details(c: SteamClient, app_id: int, country: str = "US") -> dict:
    app_id, country = _app_id(app_id), _country(country)
    d = await c.app_details(app_id, country)
    price = d.get("price_overview") or {}
    release = d.get("release_date") or {}
    return {
        "app_id": app_id,
        "name": d.get("name"),
        "type": d.get("type"),
        "short_description": clean_html(d.get("short_description")),
        "developers": d.get("developers", []),
        "publishers": d.get("publishers", []),
        "release_date": release.get("date"),
        "coming_soon": bool(release.get("coming_soon")),
        "is_free": bool(d.get("is_free")),
        "price": {
            "currency": price.get("currency"),
            "final": cents(price.get("final")),
            "initial": cents(price.get("initial")),
            "discount_percent": price.get("discount_percent", 0),
            "formatted": price.get("final_formatted"),
        } if price else None,
        "genres": [g.get("description") for g in d.get("genres", [])],
        "categories": [c_.get("description") for c_ in d.get("categories", [])][:15],
        "platforms": [p for p, on in (d.get("platforms") or {}).items() if on],
        "metacritic": (d.get("metacritic") or {}).get("score"),
        "recommendations": (d.get("recommendations") or {}).get("total"),
        "supported_languages": _languages(d.get("supported_languages")),
        "store_url": _store_url(app_id),
        "header_image": d.get("header_image"),
    }


async def do_regional_prices(c: SteamClient, app_id: int, countries: list[str] | None = None,
                             convert_to: str = "USD") -> dict:
    app_id = _app_id(app_id)
    regions = [_country(x) for x in (countries or DEFAULT_REGIONS)][:20]
    target = (convert_to or "USD").strip().upper()
    prices, rates = await asyncio.gather(asyncio.gather(*(c.price(app_id, r) for r in regions)), c.rates(target))
    rows, unavailable, free = [], [], []
    for region, p in zip(regions, prices):
        if p is None:
            unavailable.append(region)
            continue
        if not p:
            free.append(region)
            continue
        amount = cents(p.get("final"))
        converted = None
        if rates and p.get("currency") in rates and amount is not None:
            converted = round(amount / rates[p["currency"]], 2)
        rows.append({
            "country": region,
            "currency": p.get("currency"),
            "price": amount,
            "formatted": p.get("final_formatted"),
            "discount_percent": p.get("discount_percent", 0),
            f"in_{target.lower()}": converted,
        })
    key = f"in_{target.lower()}"
    comparable = [r for r in rows if r.get(key) is not None]
    comparable.sort(key=lambda r: r[key])
    result = {
        "app_id": app_id,
        "store_url": _store_url(app_id),
        "prices": comparable + [r for r in rows if r.get(key) is None],
        "not_sold_in": unavailable,
        "free_in": free,
        "rates_source": "European Central Bank via frankfurter.dev" if rates else None,
    }
    if len(comparable) >= 2:
        cheapest, dearest = comparable[0], comparable[-1]
        result["cheapest"] = cheapest["country"]
        result["most_expensive"] = dearest["country"]
        result["spread_percent"] = round(100 * (dearest[key] - cheapest[key]) / cheapest[key], 1) \
            if cheapest[key] else None
    return result


async def do_review_summary(c: SteamClient, app_id: int, language: str = "all", recent_samples: int = 3) -> dict:
    app_id = _app_id(app_id)
    data = await c.reviews(app_id, language=language or "all", samples=_clamp(recent_samples, 0, 10))
    samples = []
    for r in data.get("reviews", []):
        author = r.get("author") or {}
        samples.append({
            "recommended": bool(r.get("voted_up")),
            "text": clean_html(r.get("review"))[:400],
            "playtime_hours": round((author.get("playtime_forever") or 0) / 60, 1),
            "language": r.get("language"),
            "posted": dt.datetime.fromtimestamp(r.get("timestamp_created", 0), dt.timezone.utc).date().isoformat(),
        })
    return {"app_id": app_id, "language": language or "all", **_review_block(data.get("query_summary") or {}),
            "recent_reviews": samples}


async def do_player_count(c: SteamClient, app_id: int) -> dict:
    app_id = _app_id(app_id)
    return {"app_id": app_id, "current_players": await c.current_players(app_id),
            "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


async def do_game_news(c: SteamClient, app_id: int, count: int = 5) -> dict:
    app_id = _app_id(app_id)
    items = await c.news(app_id, _clamp(count, 1, 20))
    return {"app_id": app_id, "news": [{
        "title": n.get("title"),
        "date": dt.datetime.fromtimestamp(n.get("date", 0), dt.timezone.utc).date().isoformat(),
        "source": n.get("feedlabel"),
        "url": n.get("url"),
        "excerpt": clean_html(n.get("contents"))[:400],
    } for n in items]}


CHARTS = {"top_sellers", "new_releases", "specials", "coming_soon"}


async def do_store_charts(c: SteamClient, chart: str = "top_sellers", country: str = "US", limit: int = 10) -> dict:
    if chart not in CHARTS:
        raise ValueError(f"chart must be one of {sorted(CHARTS)}")
    country = _country(country)
    data = await c.featured(country)
    seen: set = set()
    items = [it for it in ((data.get(chart) or {}).get("items") or [])
             if not (it.get("id") in seen or seen.add(it.get("id")))][:_clamp(limit, 1, 30)]
    return {"chart": chart, "country": country, "items": [{
        "app_id": it.get("id"),
        "name": it.get("name"),
        "price": cents(it.get("final_price")),
        "currency": it.get("currency"),
        "discount_percent": it.get("discount_percent", 0),
        "store_url": _store_url(it.get("id")),
    } for it in items]}


async def do_compare_games(c: SteamClient, app_ids: list[int], country: str = "US") -> dict:
    ids = [_app_id(x) for x in dict.fromkeys(app_ids or [])][:8]
    if len(ids) < 2:
        raise ValueError("pass at least two different app_ids to compare")
    country = _country(country)

    async def one(app_id: int) -> dict:
        row: dict = {"app_id": app_id, "store_url": _store_url(app_id)}
        try:
            d = await c.app_details(app_id, country)
            price = d.get("price_overview") or {}
            row.update(name=d.get("name"), release_date=(d.get("release_date") or {}).get("date"),
                       price=price.get("final_formatted") or ("Free" if d.get("is_free") else None),
                       genres=[g.get("description") for g in d.get("genres", [])][:4])
        except SteamError as exc:
            row["error"] = str(exc)
            return row
        try:
            rv = await c.reviews(app_id)
            row.update(_review_block(rv.get("query_summary") or {}))
        except SteamError:
            pass
        try:
            row["current_players"] = await c.current_players(app_id)
        except SteamError:
            row["current_players"] = None
        return row

    return {"country": country, "games": list(await asyncio.gather(*(one(i) for i in ids)))}


# ---------------------------------------------------------------- MCP tools
async def _guard(coro):
    """Expected failures (bad input, unknown app, Steam unavailable) reach the assistant as readable errors."""
    try:
        return await coro
    except (SteamError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
async def search_games(query: str, country: str = "US", limit: int = 10) -> dict:
    """Search the Steam store by name. Returns app_id, price in the region's currency, platforms.

    country: two-letter store region (US, GB, DE, JP, KR, CN...). limit: 1-25.
    """
    return await _guard(do_search_games(client(), query, country, limit))


@mcp.tool(annotations=READ_ONLY)
async def game_details(app_id: int, country: str = "US") -> dict:
    """Store page facts for one app: description, developers, release date, price, genres, languages."""
    return await _guard(do_game_details(client(), app_id, country))


@mcp.tool(annotations=READ_ONLY)
async def regional_prices(app_id: int, countries: list[str] | None = None, convert_to: str = "USD") -> dict:
    """Compare an app's price across store regions, converted to one currency and sorted cheapest first.

    Defaults to the US, UK, Germany, France, Japan, South Korea, China, Taiwan and Hong Kong.
    Uses European Central Bank reference rates for conversion.
    """
    return await _guard(do_regional_prices(client(), app_id, countries, convert_to))


@mcp.tool(annotations=READ_ONLY)
async def review_summary(app_id: int, language: str = "all", recent_samples: int = 3) -> dict:
    """Steam review score (e.g. "Very Positive"), positive percentage, totals and a few recent reviews.

    language: "all" or a Steam language name such as english, japanese, koreana, schinese.
    """
    return await _guard(do_review_summary(client(), app_id, language, recent_samples))


@mcp.tool(annotations=READ_ONLY)
async def player_count(app_id: int) -> dict:
    """Number of players currently in the game right now."""
    return await _guard(do_player_count(client(), app_id))


@mcp.tool(annotations=READ_ONLY)
async def game_news(app_id: int, count: int = 5) -> dict:
    """Latest patch notes and news posts for a game (1-20 items)."""
    return await _guard(do_game_news(client(), app_id, count))


@mcp.tool(annotations=READ_ONLY)
async def store_charts(chart: Literal["top_sellers", "new_releases", "specials", "coming_soon"] = "top_sellers",
                       country: str = "US", limit: int = 10) -> dict:
    """Current Steam store charts for a region: top sellers, new releases, specials or coming soon."""
    return await _guard(do_store_charts(client(), chart, country, limit))


@mcp.tool(annotations=READ_ONLY)
async def compare_games(app_ids: list[int], country: str = "US") -> dict:
    """Side-by-side comparison of 2-8 games: price, release date, genres, review score, current players."""
    return await _guard(do_compare_games(client(), app_ids, country))


def main() -> None:
    parser = argparse.ArgumentParser(prog="steam-scout-mcp", description="Steam market intelligence MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio",
                        help="stdio for desktop clients (default) or streamable-http for remote use")
    parser.add_argument("--version", action="version", version=f"steam-scout-mcp {__version__}")
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
