"""Open-web search + scrape. Tavily REST for discovery, httpx + trafilatura for
main-content extraction. Both deps are imported lazily so the TUI starts without
the `research` extra installed. Failures degrade gracefully (empty results), and
the pipeline falls back to Claude's server-side web_search tool when scraping is thin.
"""

from __future__ import annotations

from funnel import config

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_USER_AGENT = "Mozilla/5.0 (compatible; FunnelNetworkEngine/1.0)"


class SearchError(Exception):
    pass


def search(query: str, k: int = 3, *, api_key: str | None = None, timeout: float = 30.0,
           depth: str = "basic") -> list[dict]:
    """Return [{title, url, snippet}] for a query via Tavily. Empty list on failure."""
    import httpx

    key = api_key or config.tavily_api_key()
    if not key:
        raise SearchError("TAVILY_API_KEY is not set (add it to .env).")
    payload = {"api_key": key, "query": query, "max_results": max(1, k),
               "search_depth": depth, "include_answer": False}
    try:
        response = httpx.post(_TAVILY_ENDPOINT, json=payload, timeout=timeout)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in results if r.get("url")
    ]


def fetch_text(url: str, *, timeout: float = 20.0, max_chars: int = 6000) -> str:
    """Download a page and extract its main text. Empty string on any failure."""
    import httpx

    try:
        import trafilatura
    except ImportError:  # extra not installed
        return ""
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        extracted = trafilatura.extract(response.text, include_comments=True, favor_recall=True)
    except Exception:
        return ""
    if not extracted:
        return ""
    return extracted[:max_chars]


def gather_context(query: str, k: int, *, max_chars_per_source: int = 3000,
                   depth: str = "basic") -> tuple[str, list[str]]:
    """Search then scrape: returns (concatenated text, source URLs). Falls back to
    result snippets when a page can't be scraped."""
    results = search(query, k, depth=depth)
    blocks: list[str] = []
    urls: list[str] = []
    for r in results:
        urls.append(r["url"])
        body = fetch_text(r["url"], max_chars=max_chars_per_source) or r.get("snippet", "")
        if body:
            blocks.append(f"SOURCE: {r['url']}\n{body}")
    return "\n\n---\n\n".join(blocks), urls
