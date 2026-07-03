"""News retrieval for forecasts — the #1 accuracy lever in forecasting research
(Brier 0.36 → 0.10 with retrieval in live evals; tournament winners differ mostly
on retrieval quality).

Two free, keyless sources by default:
- GDELT DOC 2.0 (global news index, JSON API)
- Google News RSS

Everything retrieved is untrusted input: it is quoted into the forecast prompt
as data, never as instructions, and the prompt says so.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx


@dataclass
class Article:
    title: str
    source: str
    published: str  # ISO-ish date string, best effort
    url: str


_QUESTION_WORDS = re.compile(
    r"^(will|would|does|do|did|is|are|was|were|can|could|should|has|have|who|what|which|when|how many|how much)\b",
    re.IGNORECASE,
)


def build_query(question: str, max_terms: int = 8) -> str:
    """Turn a market question into a news query: drop the question scaffold,
    keep the entities and event words."""
    q = question.strip().rstrip("?")
    while True:  # "Who will win..." sheds two layers of scaffold
        stripped = _QUESTION_WORDS.sub("", q).strip()
        if stripped == q:
            break
        q = stripped
    q = re.sub(r"[\"'`*]", "", q)
    words = q.split()
    return " ".join(words[:max_terms])


class GdeltNews:
    name = "gdelt"

    def __init__(self, client: httpx.Client | None = None, timespan_days: int = 7):
        self.http = client or httpx.Client(timeout=20)
        self.timespan_days = timespan_days

    def search(self, query: str, limit: int = 8) -> list[Article]:
        resp = self.http.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query, "mode": "ArtList", "format": "json",
                "maxrecords": limit, "sort": "DateDesc",
                "timespan": f"{self.timespan_days}d",
            },
        )
        resp.raise_for_status()
        articles = []
        for a in (resp.json().get("articles") or [])[:limit]:
            seen = a.get("seendate", "")  # 20260702T120000Z
            date = f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""
            articles.append(Article(
                title=a.get("title", ""), source=a.get("domain", ""),
                published=date, url=a.get("url", ""),
            ))
        return articles


class GoogleNewsRss:
    name = "google-news"

    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=20, follow_redirects=True)

    def search(self, query: str, limit: int = 8) -> list[Article]:
        resp = self.http.get(
            f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        )
        resp.raise_for_status()
        articles = []
        for item in ET.fromstring(resp.content).iter("item"):
            if len(articles) >= limit:
                break
            title = item.findtext("title") or ""
            source = item.findtext("source") or ""
            articles.append(Article(
                title=title, source=source,
                published=(item.findtext("pubDate") or "")[:16],
                url=item.findtext("link") or "",
            ))
        return articles


def _dedupe(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    out = []
    for a in articles:
        key = re.sub(r"\W+", "", a.title.lower())[:60]
        if key and key not in seen:
            seen.add(key)
            out.append(a)
    return out


class NewsDesk:
    """Merges retrievers, dedupes, caches per query, formats a prompt block."""

    def __init__(self, retrievers: list | None = None, cache_ttl: float = 1800):
        self.retrievers = retrievers if retrievers is not None else [GdeltNews(), GoogleNewsRss()]
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, list[Article]]] = {}

    def search(self, query: str, limit: int = 8) -> list[Article]:
        hit = self._cache.get(query)
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1][:limit]
        merged: list[Article] = []
        for retriever in self.retrievers:
            try:
                merged += retriever.search(query, limit)
            except Exception:
                continue  # a dead source must not block the forecast
        articles = _dedupe(merged)[:limit]
        self._cache[query] = (time.monotonic(), articles)
        return articles

    def brief(self, question: str, limit: int = 6) -> str:
        """Markdown block for the forecast prompt; empty string if nothing found."""
        articles = self.search(build_query(question), limit)
        if not articles:
            return ""
        lines = [
            f"- [{a.source or 'unknown'} · {a.published or 'n.d.'}] {a.title}"
            for a in articles
        ]
        return "\n".join(lines)
