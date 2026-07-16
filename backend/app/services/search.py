"""Rate-limited Tavily search with cached DuckDuckGo fallback."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from duckduckgo_search import DDGS

from app.db import get_db

INJECTION_PATTERNS = re.compile(r"(ignore\s+previous|system\s+prompt|prompt\s+leak|output\s+the\s+above|you\s+are\s+now|developer\s+mode)", re.I)


@dataclass(slots=True)
class SearchResponse:
    query: str
    source: str
    evidence: str
    degraded_reason: str | None = None
    cached: bool = False

    def render(self) -> str:
        suffix = f"\n[degraded: {self.degraded_reason}]" if self.degraded_reason else ""
        return f"[source: {self.source}]\n{self.evidence}{suffix}"


class SearchService:
    def __init__(self):
        self.tavily_key = os.getenv("TAVILY_API_KEY", "")
        self.minute_limit = 3
        self.daily_limit = 50
        self._calls: deque[float] = deque()
        self._cache: dict[str, tuple[float, SearchResponse]] = {}
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._usage_cache: tuple[float, object] | None = None

    async def start(self) -> None:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(self, query: str) -> SearchResponse:
        query = " ".join((query or "").split())
        if not query:
            return SearchResponse(query, "none", "No query provided.", "empty_query")
        if INJECTION_PATTERNS.search(query):
            return SearchResponse(query, "none", "Search query rejected by the injection filter.", "unsafe_query")
        key = query.casefold()
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            response = cached[1]
            return SearchResponse(response.query, response.source, response.evidence, response.degraded_reason, True)
        await self.start()
        allowed, reason = await self._consume_tavily_credit()
        if self.tavily_key and allowed:
            try:
                response = await self._tavily(query)
                self._cache[key] = (time.monotonic() + 600, response)
                return response
            except Exception as exc:
                reason = f"tavily_error:{type(exc).__name__}"
        else:
            reason = reason or "tavily_key_missing"
        response = await self._duckduckgo(query, reason)
        self._cache[key] = (time.monotonic() + 300, response)
        return response

    async def _consume_tavily_credit(self) -> tuple[bool, str | None]:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self.minute_limit:
                return False, "minute_limit"
            day = datetime.now(timezone.utc).date().isoformat()
            db = await get_db("steins_gate", "control")
            try:
                row = await (await db.execute("SELECT tavily_credits FROM search_usage WHERE day_utc=?", (day,))).fetchone()
                used = int(row[0]) if row else 0
                if used >= self.daily_limit:
                    return False, "daily_limit"
                await db.execute("INSERT INTO search_usage(day_utc,tavily_credits) VALUES(?,1) ON CONFLICT(day_utc) DO UPDATE SET tavily_credits=tavily_credits+1", (day,))
                await db.commit()
            finally:
                await db.close()
            self._calls.append(now)
            return True, None

    async def _tavily(self, query: str) -> SearchResponse:
        assert self._client is not None
        response = await self._client.post("https://api.tavily.com/search", json={"api_key": self.tavily_key, "query": query, "search_depth": "basic", "max_results": 5, "include_answer": True})
        response.raise_for_status()
        payload = response.json()
        evidence = []
        if payload.get("answer"):
            evidence.append(str(payload["answer"]))
        for item in payload.get("results", [])[:5]:
            evidence.append(f"Title: {item.get('title','')}\nURL: {item.get('url','')}\nContent: {item.get('content','')}")
        return SearchResponse(query, "tavily", "\n\n".join(evidence) or "No results.")

    async def _duckduckgo(self, query: str, reason: str | None) -> SearchResponse:
        def run() -> str:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            return "\n\n".join(f"Title: {row.get('title','')}\nURL: {row.get('href','')}\nContent: {row.get('body','')}" for row in results) or "No results."
        try:
            return SearchResponse(query, "duckduckgo", await asyncio.to_thread(run), reason)
        except Exception as exc:
            return SearchResponse(query, "none", f"Search unavailable: {exc}", reason or "duckduckgo_error")

    async def health(self) -> dict[str, object]:
        day = datetime.now(timezone.utc).date().isoformat()
        db = await get_db("steins_gate", "control")
        try:
            row = await (await db.execute("SELECT tavily_credits FROM search_usage WHERE day_utc=?", (day,))).fetchone()
            used = int(row[0]) if row else 0
        finally:
            await db.close()
        remote_usage = None
        if self.tavily_key:
            if self._usage_cache and self._usage_cache[0] > time.monotonic():
                remote_usage = self._usage_cache[1]
            else:
                try:
                    await self.start()
                    assert self._client is not None
                    response = await self._client.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {self.tavily_key}"}, timeout=3.0)
                    response.raise_for_status()
                    remote_usage = response.json()
                except Exception as exc:
                    remote_usage = {"error": str(exc)}
                self._usage_cache = (time.monotonic() + 60, remote_usage)
        return {"ok": True, "primary": "tavily" if self.tavily_key else "duckduckgo", "tavily_configured": bool(self.tavily_key), "local_daily_used": used, "local_daily_limit": self.daily_limit, "minute_limit": self.minute_limit, "remote_usage": remote_usage}


search_service = SearchService()


_WEATHER_KEYWORDS = re.compile(r"天气|天気|weather|temperature|forecast|气温|気温|降水|下雨|rain|snow", re.I)


def _extract_city(query: str) -> str:
    city = query
    for pattern in (
        r"今天|明天|后天|今日|明日|现在|当前|怎么样|如何",
        r"weather|temperature|forecast|today|tomorrow|now|current",
        r"天气|天気|气温|気温|降水|下雨|下雪|rain|snow",
        r"\d{4}[年/\-]\d{0,2}[月/\-]?\d{0,2}日?",
    ):
        city = re.sub(pattern, "", city, flags=re.I)
    city = re.sub(r"[的の，。？！?,.!\s_-]", "", city).strip()
    return city or "Akihabara"


def _legacy_weather(query: str) -> str:
    city = _extract_city(query)
    try:
        response = httpx.get(f"http://wttr.in/{city}?format=j1", timeout=5.0)
        if response.status_code == 200:
            payload = response.json()
            current = payload.get("current_condition", [{}])[0]
            description = (current.get("weatherDesc") or [{}])[0].get("value", "?")
            lines = [f"地点: {city}", f"当前实况: {description}, {current.get('temp_C', '?')}°C", "三天天气预报:"]
            labels = ("今天", "明天", "后天")
            for index, day in enumerate(payload.get("weather", [])[:3]):
                hourly = day.get("hourly") or []
                selected = hourly[4] if len(hourly) > 4 else (hourly[len(hourly) // 2] if hourly else {})
                day_description = (selected.get("weatherDesc") or [{}])[0].get("value", "?")
                lines.append(f" - {day.get('date','')} ({labels[index]}): {day_description}, {day.get('mintempC','?')}°C ~ {day.get('maxtempC','?')}°C")
            return "\n".join(lines)
    except Exception:
        pass
    return _legacy_ddg(f"{city} weather")


def _legacy_ddg(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return f'No search results found for the query: "{query}".'
        return "\n\n".join(f"Title: {row.get('title','')}\nContent: {row.get('body','')}" for row in results)
    except Exception as exc:
        return f'Search error: {exc}. No search results found for the query: "{query}".'


def web_search(query: str) -> str:
    """Legacy synchronous API retained for scripts/tests; chat uses SearchService."""
    if not query or not query.strip():
        return "No query provided."
    if INJECTION_PATTERNS.search(query):
        return "Error: Query contained forbidden injection patterns."
    if _WEATHER_KEYWORDS.search(query):
        return _legacy_weather(query)
    return _legacy_ddg(query)
