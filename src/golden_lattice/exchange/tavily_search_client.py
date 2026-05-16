"""TavilySearchClient — Phase 0 SearchClient backed by Tavily's HTTP API.

Routes a query string between two Tavily endpoints based on URL detection:
  - http(s) URLs → POST /extract (raw page-content fetch)
  - everything else → POST /search (ranked snippets + optional answer)

Both routings return SearchResult on success and FailedSearch on typed
failure (HTTP non-2xx, network exception, or /extract returning the URL
under failed_results). §8 no-silent-failures: failed evidence is itself
shared evidence.

Implementation notes:
  - httpx.AsyncClient is dependency-injected so tests can pass a
    MockTransport and exercise routing/parsing without network access.
  - Tavily accepts api_key in the JSON body; the client also sets a
    Content-Type header. No SDK dependency — the API surface is small
    enough to wire directly against httpx.
  - SearchResult.entry_id uses substrate's content-addressed helper.
  - URL detection is structural (urllib.parse.urlparse), not regex —
    bare-domain strings without scheme route to /search, matching the
    "we don't guess the user's intent" discipline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union
from urllib.parse import urlparse

import httpx

from golden_lattice.memory_graph.phase_0 import (
    FailedSearch,
    SearchResult,
    failed_search_id,
    search_result_id,
)


TAVILY_API_BASE = "https://api.tavily.com"
TAVILY_SEARCH_PATH = "/search"
TAVILY_EXTRACT_PATH = "/extract"


def _looks_like_url(query: str) -> bool:
    """URL detector: structural check via urlparse, not regex.

    True iff the query parses with scheme http or https and a non-empty
    netloc. Bare domains and other schemes route to /search.
    """
    if not query:
        return False
    try:
        parsed = urlparse(query)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class TavilySearchClient:
    """Phase 0 SearchClient against Tavily's HTTP API.

    Constructor:
      api_key — Tavily API key. Passed in request bodies.
      httpx_client — optional AsyncClient. If omitted, a default client is
        constructed; callers retain responsibility for closing it via
        aclose() in production.
    """

    def __init__(
        self,
        *,
        api_key: str,
        httpx_client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("TavilySearchClient requires a non-empty api_key.")
        self._api_key = api_key
        self._timeout = timeout_seconds
        if httpx_client is None:
            httpx_client = httpx.AsyncClient(base_url=TAVILY_API_BASE)
        self._http = httpx_client

    async def execute_search(
        self, query: str
    ) -> Union[SearchResult, FailedSearch]:
        if _looks_like_url(query):
            return await self._extract(query)
        return await self._search(query)

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- /search -----------------------------------------------------

    async def _search(self, query: str) -> Union[SearchResult, FailedSearch]:
        now = datetime.now(timezone.utc)
        try:
            response = await self._http.post(
                TAVILY_API_BASE + TAVILY_SEARCH_PATH,
                json={"api_key": self._api_key, "query": query},
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return FailedSearch(
                entry_id=failed_search_id(query, now),
                query=query,
                reason=f"tavily search network error: {exc}",
                attempted_at=now,
            )

        if response.status_code >= 400:
            return FailedSearch(
                entry_id=failed_search_id(query, now),
                query=query,
                reason=f"tavily search HTTP {response.status_code}: {response.text[:200]}",
                attempted_at=now,
            )

        try:
            payload = response.json()
        except Exception as exc:
            return FailedSearch(
                entry_id=failed_search_id(query, now),
                query=query,
                reason=f"tavily search response not JSON: {exc}",
                attempted_at=now,
            )

        results = payload.get("results", [])
        answer = payload.get("answer", "")
        # Result text: prefer Tavily's synthesized answer if present, then
        # concatenate result snippets. If both are empty, the search ran
        # but found nothing — typed-fail with that reason.
        snippets = "\n".join(
            f"- {r.get('title', '')}: {r.get('content', '')}"
            for r in results
            if r.get("content")
        )
        parts = [p for p in (answer, snippets) if p]
        result_text = "\n\n".join(parts)
        source_urls = tuple(r.get("url", "") for r in results if r.get("url"))

        if not result_text.strip():
            return FailedSearch(
                entry_id=failed_search_id(query, now),
                query=query,
                reason="tavily search returned no usable content",
                attempted_at=now,
            )

        return SearchResult(
            entry_id=search_result_id(query, now),
            query=query,
            result_text=result_text,
            source_urls=source_urls,
            executed_at=now,
        )

    # --- /extract ----------------------------------------------------

    async def _extract(self, url: str) -> Union[SearchResult, FailedSearch]:
        now = datetime.now(timezone.utc)
        try:
            response = await self._http.post(
                TAVILY_API_BASE + TAVILY_EXTRACT_PATH,
                json={"api_key": self._api_key, "urls": [url]},
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return FailedSearch(
                entry_id=failed_search_id(url, now),
                query=url,
                reason=f"tavily extract network error: {exc}",
                attempted_at=now,
            )

        if response.status_code >= 400:
            return FailedSearch(
                entry_id=failed_search_id(url, now),
                query=url,
                reason=f"tavily extract HTTP {response.status_code}: {response.text[:200]}",
                attempted_at=now,
            )

        try:
            payload = response.json()
        except Exception as exc:
            return FailedSearch(
                entry_id=failed_search_id(url, now),
                query=url,
                reason=f"tavily extract response not JSON: {exc}",
                attempted_at=now,
            )

        failed_results = payload.get("failed_results", [])
        for fr in failed_results:
            if fr.get("url") == url:
                reason = fr.get("error") or "tavily extract failed (no reason given)"
                return FailedSearch(
                    entry_id=failed_search_id(url, now),
                    query=url,
                    reason=f"tavily extract: {reason}",
                    attempted_at=now,
                )

        results = payload.get("results", [])
        if not results:
            return FailedSearch(
                entry_id=failed_search_id(url, now),
                query=url,
                reason="tavily extract returned no results and no failed_results",
                attempted_at=now,
            )

        # First (and typically only) result for a single-URL extract.
        first = results[0]
        raw_content = first.get("raw_content", "") or first.get("content", "")
        if not raw_content.strip():
            return FailedSearch(
                entry_id=failed_search_id(url, now),
                query=url,
                reason="tavily extract returned empty content",
                attempted_at=now,
            )

        return SearchResult(
            entry_id=search_result_id(url, now),
            query=url,
            result_text=raw_content,
            source_urls=(first.get("url", url),),
            executed_at=now,
        )
