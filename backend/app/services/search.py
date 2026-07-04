import re
import httpx
from duckduckgo_search import DDGS

# Security scan pattern to detect potential prompt injection attempts in search queries
INJECTION_PATTERNS = re.compile(
    r"(ignore\s+previous|system\s+prompt|prompt\s+leak|translate\s+to|output\s+the\s+above|you\s+are\s+now|override|developer\s+mode)",
    re.IGNORECASE
)

_WEATHER_KEYWORDS = re.compile(
    r"天气|气温|温度|降水|下雨|雪|台风|天気|気温|降水|天候|weather|temperature|forecast|rain|snow",
    re.IGNORECASE,
)

_STRIP_PATTERNS = [
    r"今天|明天|后天|每日|当日|今日|明日|後日|今週|来週|先週|現在|今日の|明日の|後日の",
    r"today|tomorrow|yesterday|now|weekly|daily|current|forecast",
    r"天气|天気|weather|気温|气温|temperature|怎么样|どう|ですか|はどう| forecast",
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
    r"\d{4}年\d{1,2}月(\d{1,2}日?)?",
    r"\d{1,2}月(\d{1,2}日?)?",
    r"\d{4}年?",
]

def _extract_city(query: str) -> str:
    city = query
    for pat in _STRIP_PATTERNS:
        city = re.sub(pat, "", city, flags=re.IGNORECASE)
    city = re.sub(r"[原そっちの、。？?,.!?!？\s\-_,]", "", city)
    city = city.strip()
    if not city or city in ("今日", "そっち"):
        city = "Akihabara"
    return city

def _search_weather(query: str) -> str:
    city = _extract_city(query)
    try:
        url = f"http://wttr.in/{city}?format=j1"
        r = httpx.get(url, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            current = data.get("current_condition", [{}])[0]
            temp = current.get("temp_C", "?")
            desc_list = current.get("weatherDesc", [{}])
            desc = desc_list[0].get("value", "?") if desc_list else "?"

            lines = [f"地点: {city}", f"当前实况: {desc}, {temp}°C", "三天天气预报:"]
            day_labels = ["今天", "明天", "后天"]
            for idx, day in enumerate(data.get("weather", [])[:3]):
                label = day_labels[idx] if idx < len(day_labels) else ""
                date = day.get("date", "")
                max_t = day.get("maxtempC", "?")
                min_t = day.get("mintempC", "?")
                hourly = day.get("hourly", [])
                day_desc = "?"
                if hourly:
                    target = hourly[4] if len(hourly) > 4 else hourly[len(hourly) // 2]
                    dl = target.get("weatherDesc", [{}])
                    day_desc = dl[0].get("value", "?") if dl else "?"
                    if day_desc == "?" and hourly[0].get("weatherDesc"):
                        day_desc = hourly[0]["weatherDesc"][0].get("value", "?")
                lines.append(f" - {date} ({label}): {day_desc}, {min_t}°C ~ {max_t}°C")
            return "\n".join(lines)
    except Exception as e:
        print(f"[Weather Search Error] {e}")

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{city} 天气", max_results=3))
            if results:
                formatted = []
                for r in results:
                    title = r.get("title", "No Title")
                    body = r.get("body", "No Body")
                    formatted.append(f"Title: {title}\nContent: {body}")
                return "\n\n".join(formatted)
    except Exception as e:
        print(f"[Weather Fallback Error] {e}")

    return f"天气查询失败：无法获取 {city} 的实时天气数据。"

def _search_general(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if not results:
                return f"No search results found for the query: \"{query}\"."
            formatted = []
            for r in results:
                title = r.get("title", "No Title")
                body = r.get("body", "No Body")
                formatted.append(f"Title: {title}\nContent: {body}")
            return "\n\n".join(formatted)
    except Exception as e:
        return f"Search error: {str(e)}. No search results found for the query: \"{query}\"."

def web_search(query: str) -> str:
    """
    Perform a web search using DuckDuckGo or wttr.in for weather.
    Filters out queries matching security injection patterns.
    """
    if not query or not query.strip():
        return "No query provided."

    if INJECTION_PATTERNS.search(query):
        return "Error: Query contained forbidden injection patterns."

    if _WEATHER_KEYWORDS.search(query):
        return _search_weather(query)

    return _search_general(query)
