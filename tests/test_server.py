"""Offline tests: Steam is replaced by httpx.MockTransport, MCP is exercised through the in-process client."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mcp import Client

from steam_scout_mcp import server
from steam_scout_mcp.steam import SteamClient

PRICES = {
    "US": {"currency": "USD", "final": 1499, "final_formatted": "$14.99", "discount_percent": 0},
    "DE": {"currency": "EUR", "final": 1399, "final_formatted": "13,99€", "discount_percent": 0},
    "JP": {"currency": "JPY", "final": 148000, "final_formatted": "¥ 1,480", "discount_percent": 0},
    "KR": {"currency": "KRW", "final": 1600000, "final_formatted": "₩ 16,000", "discount_percent": 0},
    "CN": {"currency": "CNY", "final": 4800, "final_formatted": "¥ 48.00", "discount_percent": 0},
}
RATES = {"base": "USD", "rates": {"EUR": 0.88, "JPY": 157.0, "KRW": 1355.4, "CNY": 6.7}}


def handler(request: httpx.Request) -> httpx.Response:
    url, q = request.url, request.url.params
    if url.host == "api.frankfurter.dev":
        return httpx.Response(200, json=RATES)
    if url.path == "/api/storesearch/":
        return httpx.Response(200, json={"total": 1, "items": [{
            "id": 413150, "name": "Stardew Valley", "price": {"currency": "USD", "final": 1499},
            "platforms": {"windows": True, "mac": True, "linux": True}, "metascore": "89"}]})
    if url.path == "/api/appdetails":
        app = q["appids"]
        if app == "1":
            return httpx.Response(200, json={"1": {"success": False}})
        if q.get("filters") == "price_overview":
            cc = q["cc"]
            if cc == "XX":
                return httpx.Response(200, json={app: {"success": False}})
            return httpx.Response(200, json={app: {"success": True, "data": {"price_overview": PRICES[cc]}}})
        return httpx.Response(200, json={app: {"success": True, "data": {
            "name": "Stardew Valley", "type": "game", "short_description": "Farming <b>RPG</b> &amp; life sim",
            "developers": ["ConcernedApe"], "publishers": ["ConcernedApe"],
            "release_date": {"date": "26 Feb, 2016", "coming_soon": False}, "is_free": False,
            "price_overview": PRICES["US"], "genres": [{"description": "RPG"}, {"description": "Simulation"}],
            "categories": [{"description": "Single-player"}], "platforms": {"windows": True, "mac": False},
            "supported_languages": "English<strong>*</strong>, Japanese, Korean", "metacritic": {"score": 89},
            "recommendations": {"total": 600000}, "header_image": "https://example/header.jpg"}}})
    if url.path.startswith("/appreviews/"):
        return httpx.Response(200, json={"success": 1, "query_summary": {
            "review_score_desc": "Overwhelmingly Positive", "total_positive": 980, "total_negative": 20,
            "total_reviews": 1000}, "reviews": [{"voted_up": True, "review": "Great <i>game</i>",
                                                 "author": {"playtime_forever": 600}, "language": "english",
                                                 "timestamp_created": 1759200000}]})
    if "GetNumberOfCurrentPlayers" in url.path:
        return httpx.Response(200, json={"response": {"player_count": 42000, "result": 1}})
    if "GetNewsForApp" in url.path:
        return httpx.Response(200, json={"appnews": {"newsitems": [{
            "title": "Patch 1.6", "date": 1759200000, "feedlabel": "Community Announcements",
            "url": "https://example/news", "contents": "Fixes &amp; <b>features</b>"}]}})
    if url.path == "/api/featuredcategories":
        return httpx.Response(200, json={"top_sellers": {"items": [
            {"id": 730, "name": "Counter-Strike 2", "final_price": 0, "currency": "USD", "discount_percent": 0}]}})
    return httpx.Response(404)


@pytest.fixture()
def steam():
    return SteamClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def run(coro):
    return asyncio.run(coro)


def test_regional_prices_sorted_and_converted(steam):
    res = run(server.do_regional_prices(steam, 413150, ["US", "DE", "JP", "KR", "CN", "XX"]))
    order = [p["country"] for p in res["prices"]]
    assert order[0] == "CN" and res["cheapest"] == "CN"          # 48 CNY ≈ $7.16
    assert res["not_sold_in"] == ["XX"]
    jp = next(p for p in res["prices"] if p["country"] == "JP")
    assert jp["price"] == 1480.0 and jp["in_usd"] == pytest.approx(9.43, abs=0.01)
    assert res["spread_percent"] > 0


def test_details_cleans_html(steam):
    d = run(server.do_game_details(steam, 413150, "us"))
    assert d["short_description"] == "Farming RPG & life sim"
    assert d["supported_languages"][:2] == ["English", "Japanese"]
    assert d["price"]["final"] == 14.99


def test_reviews_players_news_charts(steam):
    r = run(server.do_review_summary(steam, 413150))
    assert r["positive_percent"] == 98.0 and r["recent_reviews"][0]["playtime_hours"] == 10.0
    assert run(server.do_player_count(steam, 413150))["current_players"] == 42000
    assert run(server.do_game_news(steam, 413150))["news"][0]["excerpt"] == "Fixes & features"
    assert run(server.do_store_charts(steam))["items"][0]["name"] == "Counter-Strike 2"


def test_compare_reports_missing_apps(steam):
    res = run(server.do_compare_games(steam, [413150, 1]))
    good, bad = res["games"]
    assert good["current_players"] == 42000 and good["positive_percent"] == 98.0
    assert "not found" in bad["error"]


@pytest.mark.parametrize("bad", [
    lambda s: server.do_search_games(s, "  "),
    lambda s: server.do_game_details(s, -5),
    lambda s: server.do_store_charts(s, "unknown"),
    lambda s: server.do_regional_prices(s, 413150, ["USA"]),
    lambda s: server.do_compare_games(s, [413150]),
])
def test_input_validation(steam, bad):
    with pytest.raises(ValueError):
        run(bad(steam))


def test_mcp_protocol_end_to_end(steam, monkeypatch):
    monkeypatch.setattr(server, "_client", steam)

    async def scenario():
        async with Client(server.mcp) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            assert tools == {"search_games", "game_details", "regional_prices", "review_summary",
                             "player_count", "game_news", "store_charts", "compare_games"}
            res = await c.call_tool("search_games", {"query": "stardew"})
            assert not res.is_error
            payload = res.structured_content or json.loads(res.content[0].text)
            assert payload["results"][0]["app_id"] == 413150
            bad = await c.call_tool("game_details", {"app_id": 1})
            assert bad.is_error

    asyncio.run(scenario())
