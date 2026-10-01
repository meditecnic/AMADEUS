"""Rate-limited multi-provider web search (Tavily / Firecrawl only; no DDG fallback)."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
try:
    # Legacy sync web_search() helper only; chat path never uses DDG.
    from ddgs import DDGS
except ImportError:  # pragma: no cover - compatibility for an old install
    from duckduckgo_search import DDGS

from app.db import get_db
from app.services.credentials import credential_store

SearchProviderId = Literal["tavily", "firecrawl"]

SEARCH_PROVIDERS: tuple[SearchProviderId, ...] = ("tavily", "firecrawl")
# Legacy single-key id remains the Tavily slot so existing installs keep working.
SEARCH_CREDENTIAL_ID = "search.tavily"
SEARCH_CREDENTIAL_BY_PROVIDER: dict[SearchProviderId, str] = {
    "tavily": "search.tavily",
    "firecrawl": "search.firecrawl",
}
SEARCH_ACTIVE_PROVIDER_ID = "search.active_provider"
SEARCH_ENV_KEYS: dict[SearchProviderId, str] = {
    "tavily": "TAVILY_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
}

INJECTION_PATTERNS = re.compile(
    r"(ignore\s+previous|system\s+prompt|prompt\s+leak|output\s+the\s+above|"
    r"you\s+are\s+now|developer\s+mode)",
    re.I,
)

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


def normalize_search_provider(value: str | None) -> SearchProviderId:
    candidate = (value or "").strip().lower()
    if candidate in SEARCH_PROVIDERS:
        return candidate  # type: ignore[return-value]
    return "tavily"


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
    def configured_key(provider: SearchProviderId) -> str:
        """Read the latest write-only credential without requiring a restart."""

        cred_id = SEARCH_CREDENTIAL_BY_PROVIDER[provider]
        try:
            stored = credential_store.get(cred_id)
        except Exception:
            stored = None
        return stored or os.getenv(SEARCH_ENV_KEYS[provider], "")

    @staticmethod
    def configured_tavily_key() -> str:
        """Backward-compatible alias for tests and older call sites."""

        return SearchService.configured_key("tavily")

    @staticmethod
    def get_active_provider() -> SearchProviderId:
        try:
            stored = credential_store.get(SEARCH_ACTIVE_PROVIDER_ID)
        except Exception:
            stored = None
        return normalize_search_provider(stored)

    @staticmethod
    def set_active_provider(provider: SearchProviderId) -> SearchProviderId:
        normalized = normalize_search_provider(provider)
        credential_store.set(SEARCH_ACTIVE_PROVIDER_ID, normalized)
        return normalized

    def provider_configured(self, provider: SearchProviderId) -> bool:
        return bool(self.configured_key(provider))

    def any_paid_provider_configured(self) -> bool:
        return any(self.provider_configured(provider) for provider in SEARCH_PROVIDERS)

    def credentials_changed(self) -> None:
        """Discard results and usage state tied to the previous credential."""

        self._cache.clear()
        self._usage_cache = None

    def settings_snapshot(self) -> dict[str, Any]:
        active = self.get_active_provider()
        return {
            "active_provider": active,
            "providers": {
                "tavily": {
                    "id": "tavily",
                    "label": "Tavily",
                    "configured": self.provider_configured("tavily"),
                    "docs_url": "https://app.tavily.com/home",
                    "hint": "面向 AI 检索；免费额度按官方控制台为准。",
                },
                "firecrawl": {
                    "id": "firecrawl",
                    "label": "Firecrawl",
                    "configured": self.provider_configured("firecrawl"),
                    "docs_url": "https://www.firecrawl.dev/app/api-keys",
                    "hint": "Search + 页面内容抽取；免费额度按官方控制台为准。",
                },
            },
            "fallback": None,
            "fallback_note": "请至少配置一个搜索提供商并设为当前使用。",
            "configured": self.provider_configured(active) or self.any_paid_provider_configured(),
            # Back-compat fields for older desktop clients.
            "provider": active,
        }

    async def start(self) -> None:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _provider_order(self) -> list[SearchProviderId]:
        active = self.get_active_provider()
        order: list[SearchProviderId] = [active]
        for provider in SEARCH_PROVIDERS:
            if provider not in order:
                order.append(provider)
        return order

    async def search(self, query: str) -> SearchResponse:
        query = " ".join((query or "").split())
        if not query:
            return SearchResponse(query, "none", "No query provided.", "empty_query")
        if INJECTION_PATTERNS.search(query):
            return SearchResponse(
                query,
                "none",
                "Search query rejected by the injection filter.",
                "unsafe_query",
            )
        provider_query = prepare_search_query(query)
        key = f"{self.get_active_provider()}::{query.casefold()}"
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            response = cached[1]
            return SearchResponse(
                response.query,
                response.source,
                response.evidence,
                response.degraded_reason,
                True,
            )
        await self.start()

        last_reason: str | None = None
        for provider in self._provider_order():
            api_key = self.configured_key(provider)
            if not api_key:
                last_reason = f"{provider}_key_missing"
                continue
            allowed, reason = await self._consume_search_credit()
            if not allowed:
                last_reason = reason or "rate_limited"
                continue
            try:
                if provider == "tavily":
                    response = await self._tavily(provider_query, api_key)
                else:
                    response = await self._firecrawl(provider_query, api_key)
                if response.failure_code is None:
                    self._cache[key] = (time.monotonic() + 600, response)
                    return SearchResponse(
                        query,
                        response.source,
                        response.evidence,
                        response.degraded_reason,
                    )
                last_reason = response.degraded_reason or response.failure_code
            except Exception as exc:
                last_reason = f"{provider}_error:{type(exc).__name__}"

        # No DuckDuckGo (or other free HTML) degradation path — fail closed.
        reason = last_reason or "no_paid_provider"
        response = SearchResponse(
            query,
            "none",
            "Web Search unavailable: configure Tavily or Firecrawl.",
            reason,
        )
        self._cache[key] = (time.monotonic() + 120, response)
        return response

    async def _consume_search_credit(self) -> tuple[bool, str | None]:
        """Shared local rate limit for all paid search providers."""
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self.minute_limit:
                return False, "minute_limit"
            day = datetime.now(timezone.utc).date().isoformat()
            db = await get_db("steins_gate", "control")
            try:
                row = await (
                    await db.execute(
                        "SELECT tavily_credits FROM search_usage WHERE day_utc=?",
                        (day,),
                    )
                ).fetchone()
                used = int(row[0]) if row else 0
                if used >= self.daily_limit:
                    return False, "daily_limit"
                await db.execute(
                    "INSERT INTO search_usage(day_utc,tavily_credits) VALUES(?,1) "
                    "ON CONFLICT(day_utc) DO UPDATE SET tavily_credits=tavily_credits+1",
                    (day,),
                )
                await db.commit()
            finally:
                await db.close()
            self._calls.append(now)
            return True, None

    # Backward-compatible name used by older tests.
    async def _consume_tavily_credit(self) -> tuple[bool, str | None]:
        return await self._consume_search_credit()

    async def _tavily(self, query: str, api_key: str) -> SearchResponse:
        assert self._client is not None
        response = await self._client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        evidence: list[str] = []
        if payload.get("answer"):
            evidence.append(str(payload["answer"]))
        for item in payload.get("results", [])[:5]:
            evidence.append(
                f"Title: {item.get('title', '')}\n"
                f"URL: {item.get('url', '')}\n"
                f"Content: {item.get('content', '')}"
            )
        return SearchResponse(query, "tavily", "\n\n".join(evidence) or "No results.")

    async def _firecrawl(self, query: str, api_key: str) -> SearchResponse:
        """Firecrawl /v2/search — Bearer auth, returns web hits with snippets/markdown."""
        assert self._client is not None
        response = await self._client.post(
            "https://api.firecrawl.dev/v2/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "limit": 5},
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        evidence = self._format_firecrawl_evidence(payload)
        return SearchResponse(query, "firecrawl", evidence or "No results.")

    @staticmethod
    def _format_firecrawl_evidence(payload: dict[str, Any]) -> str:
        data = payload.get("data", payload)
        rows: list[Any]
        if isinstance(data, dict):
            rows = data.get("web") or data.get("results") or data.get("organic") or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        chunks: list[str] = []
        for item in rows[:5]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name") or ""
            url = item.get("url") or item.get("link") or item.get("href") or ""
            content = (
                item.get("description")
                or item.get("snippet")
                or item.get("markdown")
                or item.get("content")
                or item.get("text")
                or ""
            )
            if isinstance(content, str) and len(content) > 1200:
                content = content[:1200] + "…"
            chunks.append(f"Title: {title}\nURL: {url}\nContent: {content}")
        return "\n\n".join(chunks)

    async def health(self) -> dict[str, object]:
        day = datetime.now(timezone.utc).date().isoformat()
        db = await get_db("steins_gate", "control")
        try:
            row = await (
                await db.execute(
                    "SELECT tavily_credits FROM search_usage WHERE day_utc=?",
                    (day,),
                )
            ).fetchone()
            used = int(row[0]) if row else 0
        finally:
            await db.close()
        active = self.get_active_provider()
        if self.provider_configured(active):
            primary = active
        elif self.provider_configured("tavily"):
            primary = "tavily"
        elif self.provider_configured("firecrawl"):
            primary = "firecrawl"
        else:
            primary = "none"
        return {
            "ok": True,
            "primary": primary,
            "active_provider": active,
            "tavily_configured": self.provider_configured("tavily"),
            "firecrawl_configured": self.provider_configured("firecrawl"),
            "fallback": None,
            "local_daily_used": used,
            "local_daily_limit": self.daily_limit,
            "minute_limit": self.minute_limit,
        }


search_service = SearchService()


_WEATHER_KEYWORDS = re.compile(
    r"天气|天気|weather|temperature|forecast|气温|気温|降水|下雨|rain|snow",
    re.I,
)


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
            lines = [
                f"地点: {city}",
                f"当前实况: {description}, {current.get('temp_C', '?')}°C",
                "三天天气预报:",
            ]
            labels = ("今天", "明天", "后天")
            for index, day in enumerate(payload.get("weather", [])[:3]):
                hourly = day.get("hourly") or []
                selected = (
                    hourly[4] if len(hourly) > 4
                    else (hourly[len(hourly) // 2] if hourly else {})
                )
                day_description = (selected.get("weatherDesc") or [{}])[0].get("value", "?")
                lines.append(
                    f" - {day.get('date','')} ({labels[index]}): "
                    f"{day_description}, {day.get('mintempC','?')}°C ~ {day.get('maxtempC','?')}°C"
                )
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
        return "\n\n".join(
            f"Title: {row.get('title','')}\nContent: {row.get('body','')}"
            for row in results
        )
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
