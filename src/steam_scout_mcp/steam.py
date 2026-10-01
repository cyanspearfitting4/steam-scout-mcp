"""Async client for the public, keyless Steam Store and Steam Web API endpoints.

Only endpoints that work without an API key are used, so the server runs out of the box.
Responses are cached for a few minutes and concurrency is capped to stay polite with Steam.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any

import httpx

from . import __version__

STORE = "https://store.steampowered.com"
WEBAPI = "https://api.steampowered.com"
RATES_URL = "https://api.frankfurter.dev/v1/latest"   # ECB reference rates, no key required
USER_AGENT = f"steam-scout-mcp/{__version__}"

# United States, Europe and East Asia by default
DEFAULT_REGIONS = ("US", "GB", "DE", "FR", "JP", "KR", "CN", "TW", "HK")

_TAG_RE = re.compile(r"<[^>]+>")


class SteamError(Exception):
    """A user-facing error (bad app id, Steam unavailable, region lock...)."""


def clean_html(text: str | None) -> str:
    """Strip HTML tags and entities that Steam puts into descriptions and language lists."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text.replace("<br>", "\n").replace("<br/>", "\n"))
    return re.sub(r"[ \t]+", " ", html.unescape(text)).strip()


def cents(value: int | float | None) -> float | None:
    """Steam reports every currency (USD, JPY, KRW...) in hundredths."""
    return None if value is None else round(value / 100, 2)


class SteamClient:
    def __init__(self, http: httpx.AsyncClient | None = None, cache_ttl: float = 300.0, max_concurrency: int = 4):
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = cache_ttl
        self._sem = asyncio.Semaphore(max_concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport
    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        async with self._sem:
            for attempt in range(3):
                try:
                    resp = await self._http.get(url, params=params)
                except httpx.TransportError as exc:
                    if attempt == 2:
                        raise SteamError(f"Network error while contacting Steam: {exc}") from exc
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt == 2:
                        raise SteamError(f"Steam answered HTTP {resp.status_code}; please retry in a minute")
                    retry = resp.headers.get("retry-after", "")
                    await asyncio.sleep(float(retry) if retry.isdigit() else 2.0 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise SteamError(f"Steam answered HTTP {resp.status_code}")
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise SteamError("Steam returned a response that is not JSON") from exc
                self._cache[key] = (time.monotonic(), data)
                return data
        raise SteamError("Steam is not responding")  # pragma: no cover

    # ------------------------------------------------------------------ store
    async def search(self, term: str, country: str = "US", lang: str = "english") -> list[dict]:
        data = await self._get_json(f"{STORE}/api/storesearch/", {"term": term, "l": lang, "cc": country})
        return data.get("items", []) if isinstance(data, dict) else []

    async def app_details(self, app_id: int, country: str = "US", lang: str = "english") -> dict:
        data = await self._get_json(f"{STORE}/api/appdetails", {"appids": app_id, "cc": country, "l": lang})
        entry = (data or {}).get(str(app_id)) or {}
        if not entry.get("success") or not isinstance(entry.get("data"), dict):
            raise SteamError(f"App {app_id} was not found or is not sold in region {country}")
        return entry["data"]

    async def price(self, app_id: int, country: str) -> dict | None:
        """price_overview for one region; {} for free apps, None when the app is not sold there."""
        data = await self._get_json(f"{STORE}/api/appdetails",
                                    {"appids": app_id, "cc": country, "filters": "price_overview"})
        entry = (data or {}).get(str(app_id)) or {}
        if not entry.get("success"):
            return None
        payload = entry.get("data")
        return payload.get("price_overview", {}) if isinstance(payload, dict) else {}

    async def reviews(self, app_id: int, language: str = "all", samples: int = 0) -> dict:
        data = await self._get_json(f"{STORE}/appreviews/{app_id}", {
            "json": 1, "language": language, "purchase_type": "all", "filter": "recent",
            "num_per_page": samples,
        })
        if not isinstance(data, dict) or data.get("success") != 1:
            raise SteamError(f"Reviews for app {app_id} are unavailable")
        return data

    async def featured(self, country: str = "US", lang: str = "english") -> dict:
        data = await self._get_json(f"{STORE}/api/featuredcategories", {"cc": country, "l": lang})
        if not isinstance(data, dict):
            raise SteamError("Steam store charts are unavailable right now")
        return data

    # ------------------------------------------------------------------ web api
    async def current_players(self, app_id: int) -> int:
        data = await self._get_json(f"{WEBAPI}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": app_id})
        resp = (data or {}).get("response", {})
        if resp.get("result") != 1:
            raise SteamError(f"App {app_id} does not report a current player count")
        return int(resp.get("player_count", 0))

    async def news(self, app_id: int, count: int = 5) -> list[dict]:
        data = await self._get_json(f"{WEBAPI}/ISteamNews/GetNewsForApp/v2/",
                                    {"appid": app_id, "count": count, "maxlength": 400, "format": "json"})
        return ((data or {}).get("appnews") or {}).get("newsitems", [])

    # ------------------------------------------------------------------ currency
    async def rates(self, base: str = "USD") -> dict[str, float] | None:
        """ECB reference rates (units of each currency per 1 `base`). None if unavailable."""
        try:
            data = await self._get_json(RATES_URL, {"base": base})
        except SteamError:
            return None
        rates = data.get("rates") if isinstance(data, dict) else None
        if not isinstance(rates, dict):
            return None
        return {base: 1.0, **{k: float(v) for k, v in rates.items()}}
