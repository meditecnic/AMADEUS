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
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
try:
    # `duckduckgo_search` 8.x delegates text search through Bing. The project
    # was renamed to `ddgs`, which keeps the same public DDGS API and uses the
    # maintained multi-backend implementation.
    from ddgs import DDGS
except ImportError:  # pragma: no cover - compatibility for an old install
    from duckduckgo_search import DDGS

from app.db import get_db
from app.services.credentials import credential_store

SEARCH_CREDENTIAL_ID = "search.tavily"

INJECTION_PATTERNS = re.compile(r"(ignore\s+previous|system\s+prompt|prompt\s+leak|output\s+the\s+above|you\s+are\s+now|developer\s+mode)", re.I)

_SEARCH_FAILURE_MESSAGES = {
    "empty_query": "Web Search 没有收到有效查询。",
    "unsafe_query": "Web Search 请求未通过安全校验。",
    "no_results": "Web Search 没有返回可验证的结果，本轮未生成答案。",
    "unavailable": "Web Search 当前不可用，本轮未生成答案。",
}
_WORLD_CUP_RESULT = re.compile(
    r"(?:世界杯|world\s+cup|fifa)"
    r".*(?:冠军|冠军是|winner|champion|won|夺冠|结果|result)",
    re.I,
)
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_RELATIVE_YEAR = re.compile(r"今年|本届|this\s+year|current", re.I)


class SearchEvidenceError(RuntimeError):
    """A stable, redacted failure raised when search produced no usable evidence."""

    def __init__(self, code: str):
        self.code = code if code in _SEARCH_FAILURE_MESSAGES else "unavailable"
        super().__init__(_SEARCH_FAILURE_MESSAGES[self.code])


def prepare_search_query(query: str, now: datetime | None = None) -> str:
    """Resolve current World Cup questions to a precise, authoritative query."""

    normalized = " ".join((query or "").split())
    if not _WORLD_CUP_RESULT.search(normalized):
        return normalized
    if re.search(r"历史|历届|all[- ]time|history", normalized, re.I):
        return normalized
    year_match = _YEAR.search(normalized)
    year = year_match.group(1) if year_match else str((now or datetime.now()).year)
    return (
        f"{year} FIFA World Cup final winner champion official result "
        "site:fifa.com"
    )


@dataclass(slots=True)
class SearchResponse:
    query: str
    source: str
    evidence: str
    degraded_reason: str | None = None
    cached: bool = False

    @property
    def failure_code(self) -> str | None:
        """Return a stable failure code instead of treating status text as evidence."""

        if self.degraded_reason in {"empty_query", "unsafe_query"}:
            return self.degraded_reason
        if self.source == "none":
            return "unavailable"
        if not self.evidence.strip() or self.evidence.strip().casefold() == "no results.":
            return "no_results"
        return None

    def render(self) -> str:
        suffix = f"\n[degraded: {self.degraded_reason}]" if self.degraded_reason else ""
        return f"[source: {self.source}]\n{self.evidence}{suffix}"


class SearchService:
    def __init__(self):
        self.minute_limit = 3
        self.daily_limit = 50
        self._calls: deque[float] = deque()
        self._cache: dict[str, tuple[float, SearchResponse]] = {}
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._usage_cache: tuple[float, object] | None = None

    @staticmethod
    def configured_tavily_key() -> str:
        """Read the latest write-only credential without requiring a restart."""

        try:
            stored = credential_store.get(SEARCH_CREDENTIAL_ID)
        except Exception:
            stored = None
        return stored or os.getenv("TAVILY_API_KEY", "")

    def credentials_changed(self) -> None:
        """Discard results and usage state tied to the previous credential."""

        self._cache.clear()
        self._usage_cache = None

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
        provider_query = prepare_search_query(query)
        key = query.casefold()
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            response = cached[1]
            return SearchResponse(response.query, response.source, response.evidence, response.degraded_reason, True)
        await self.start()
        tavily_key = self.configured_tavily_key()
        allowed, reason = (False, None)
        if tavily_key:
            allowed, reason = await self._consume_tavily_credit()
        if tavily_key and allowed:
            try:
                response = await self._tavily(provider_query, tavily_key)
                self._cache[key] = (time.monotonic() + 600, response)
                return SearchResponse(query, response.source, response.evidence, response.degraded_reason)
            except Exception as exc:
                reason = f"tavily_error:{type(exc).__name__}"
        else:
            reason = reason or "tavily_key_missing"
        response = await self._duckduckgo(provider_query, reason)
        self._cache[key] = (time.monotonic() + 300, response)
        return SearchResponse(query, response.source, response.evidence, response.degraded_reason)

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

    async def _tavily(self, query: str, api_key: str) -> SearchResponse:
        assert self._client is not None
        response = await self._client.post("https://api.tavily.com/search", json={"api_key": api_key, "query": query, "search_depth": "basic", "max_results": 5, "include_answer": True})
        response.raise_for_status()
        payload = response.json()
        evidence = []
        if payload.get("answer"):
            evidence.append(str(payload["answer"]))
        for item in payload.get("results", [])[:5]:
            evidence.append(f"Title: {item.get('title','')}\nURL: {item.get('url','')}\nContent: {item.get('content','')}")
        return SearchResponse(query, "tavily", "\n\n".join(evidence) or "No results.")

    async def _duckduckgo(self, query: str, reason: str | None) -> SearchResponse:
        def curl_results(candidate: str) -> list[dict[str, str]]:
            response = curl_requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": candidate, "b": "", "kl": "", "kp": "-1"},
                impersonate="chrome131",
                timeout=30,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            parsed = []
            for result in soup.select(".result"):
                title_link = result.select_one(".result__title a")
                if title_link is None or "y.js" in title_link.get("href", ""):
                    continue
                snippet = result.select_one(".result__snippet")
                parsed.append({
                    "title": title_link.get_text(" ", strip=True),
                    "href": title_link.get("href", ""),
                    "body": snippet.get_text(" ", strip=True) if snippet else "",
                })
                if len(parsed) >= 5:
                    break
            return parsed

        def run() -> str:
            with DDGS() as ddgs:
                queries = [query]
                if "site:fifa.com" in query:
                    queries.append(query.replace(" site:fifa.com", ""))
                results = []
                last_error = None
                for candidate in queries:
                    try:
                        try:
                            results = list(ddgs.text(
                                candidate,
                                max_results=5,
                                backend="duckduckgo",
                            ))
                        except TypeError:
                            # Older duckduckgo_search compatibility.
                            results = list(ddgs.text(candidate, max_results=5))
                        if results:
                            break
                    except Exception as exc:
                        last_error = exc
                if not results:
                    for candidate in queries:
                        try:
                            results = curl_results(candidate)
                            if results:
                                break
                        except Exception as exc:
                            last_error = exc
                if not results and last_error is not None:
                    raise last_error
            return "\n\n".join(
                f"Rank: {index}\n"
                f"Title: {row.get('title','')}\n"
                f"URL: {row.get('href','')}\n"
                f"Published: {row.get('date') or row.get('published') or 'unknown'}\n"
                f"Content: {row.get('body','')}"
                for index, row in enumerate(results, 1)
            ) or "No results."
        try:
            return SearchResponse(query, "duckduckgo", await asyncio.to_thread(run), reason)
        except Exception as exc:
            return SearchResponse(query, "none", f"Search unavailable: {exc}", reason or "duckduckgo_error")

    async def health(self) -> dict[str, object]:
        tavily_key = self.configured_tavily_key()
        day = datetime.now(timezone.utc).date().isoformat()
        db = await get_db("steins_gate", "control")
        try:
            row = await (await db.execute("SELECT tavily_credits FROM search_usage WHERE day_utc=?", (day,))).fetchone()
            used = int(row[0]) if row else 0
        finally:
            await db.close()
        remote_usage = None
        if tavily_key:
            if self._usage_cache and self._usage_cache[0] > time.monotonic():
                remote_usage = self._usage_cache[1]
            else:
                try:
                    await self.start()
                    assert self._client is not None
                    response = await self._client.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {tavily_key}"}, timeout=3.0)
                    response.raise_for_status()
                    remote_usage = response.json()
                except Exception as exc:
                    remote_usage = {"error": str(exc)}
                self._usage_cache = (time.monotonic() + 60, remote_usage)
        return {"ok": True, "primary": "tavily" if tavily_key else "duckduckgo", "tavily_configured": bool(tavily_key), "local_daily_used": used, "local_daily_limit": self.daily_limit, "minute_limit": self.minute_limit, "remote_usage": remote_usage}


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
