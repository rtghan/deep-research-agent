"""
Web search tool — multi-backend with graceful fallback.

Design decision: The brief requires "at least one real external data source."
Web search + arXiv gives two complementary sources (web + academic) enabling
triangulation and cross-source verification (Track B).

Backend selection (env var SEARCH_BACKEND, default "auto"):
  - "auto" (default): tries DuckDuckGo HTML, then Wikipedia API, then returns
    whatever it has. No API key needed.
  - "tavily": AI-optimized results; requires TAVILY_API_KEY. Falls back to
    auto if no key is set.
  - "duckduckgo": DuckDuckGo HTML only.
  - "wikipedia": Wikipedia API only.

DuckDuckGo uses the HTML endpoint (html.duckduckgo.com) with BeautifulSoup.
Wikipedia API is always reliable, always free, no key. Together they give
a robust no-cost web search. For higher-quality results later, switch
SEARCH_BACKEND=tavily and set TAVILY_API_KEY.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    source_type: str = "web"


def _search_tavily(query: str, max_results: int) -> list[SearchResult]:
    """Tavily API backend — requires TAVILY_API_KEY."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "include_raw_content": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                source_type="web",
            )
            for r in data.get("results", [])
        ]
    except Exception:
        return []


def _unwrap_ddg_url(href: str) -> str:
    """Extract the real URL from a DuckDuckGo redirect link."""
    import urllib.parse as up
    if "uddg=" in href:
        parsed = up.parse_qs(up.urlparse(href).query)
        if "uddg" in parsed:
            return up.unquote(parsed["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


def _search_duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    """
    DuckDuckGo HTML backend — no API key required.
    May be rate-limited from some IPs; caller should fall back to Wikipedia.
    """
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "b": ""},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", class_=re.compile(r"result__a")):
            if len(results) >= max_results:
                break
            title = a.get_text(strip=True)
            href = a.get("href", "")
            url = _unwrap_ddg_url(href)
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            snippet = ""
            parent = a.find_parent("div", class_=re.compile(r"result"))
            if parent:
                snippet_el = parent.find("a", class_=re.compile(r"result__snippet"))
                if snippet_el:
                    snippet = snippet_el.get_text(strip=True)
            results.append(SearchResult(
                title=title,
                url=url,
                content=snippet,
                source_type="web",
            ))
    except Exception:
        pass
    return results


def _search_wikipedia(query: str, max_results: int) -> list[SearchResult]:
    """
    Wikipedia API backend — always free, no key, reliable.
    Returns encyclopedia articles with snippets.
    Uses the MediaWiki action API with proper User-Agent.
    """
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": max_results,
                "format": "json",
            },
            headers={
                "User-Agent": "deep-research-agent/0.1 (research agent)"
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        search_results = data.get("query", {}).get("search", [])
        results: list[SearchResult] = []
        for r in search_results:
            title = r.get("title", "")
            snippet_raw = r.get("snippet", "")
            # Strip HTML tags from snippet
            snippet = BeautifulSoup(snippet_raw, "html.parser").get_text(strip=True)
            page_id = r.get("pageid", 0)
            url = f"https://en.wikipedia.org/?curid={page_id}" if page_id else (
                f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
            )
            results.append(SearchResult(
                title=title,
                url=url,
                content=snippet,
                source_type="web",
            ))
        return results
    except Exception:
        return []


def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """
    Search the web using the configured backend.

    Backend is selected via SEARCH_BACKEND env var (default: auto).
    - auto: tries DDG, then Wikipedia, returns combined results.
    - tavily: uses Tavily if key available, falls back to auto.
    - duckduckgo / wikipedia: single backend only.
    """
    backend = os.environ.get("SEARCH_BACKEND", "auto").lower()

    if backend == "tavily":
        results = _search_tavily(query, max_results)
        if results:
            return results
        # Fall back to auto
        backend = "auto"

    if backend == "duckduckgo":
        return _search_duckduckgo(query, max_results)

    if backend == "wikipedia":
        return _search_wikipedia(query, max_results)

    # auto: try DDG first, then Wikipedia
    results = _search_duckduckgo(query, max_results)
    if len(results) < max_results:
        # Fill remaining slots with Wikipedia results
        needed = max_results - len(results)
        wiki_results = _search_wikipedia(query, needed)
        # Avoid URL duplicates
        seen = {r.url for r in results}
        for r in wiki_results:
            if r.url not in seen:
                results.append(r)
                seen.add(r.url)
    return results[:max_results]
