"""Tests for TavilySearchClient — search + extract routing.

The substrate keeps InvestigationProposal.queries as tuple[str, ...] —
either search terms or URLs. The TavilySearchClient routes internally:
URL-shaped queries go to Tavily /extract (raw content fetch); other
strings go to /search (ranked snippets). Both return a SearchResult or
FailedSearch matching the Phase 0 substrate schema.

The motivating failure (session_20260516_065836_1f635cdf, GitHub URL
prompt) needed the fetch primitive specifically; a search-only client
would not have closed the gap. These tests exercise both routings
with httpx.MockTransport so no network access is required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx

from golden_lattice.exchange.tavily_search_client import (
    TavilySearchClient,
    _looks_like_url,
)
from golden_lattice.memory_graph.phase_0 import FailedSearch, SearchResult


# --- URL detection (the routing key) -------------------------------------


def test_url_detection_accepts_https():
    assert _looks_like_url("https://github.com/templetwo")


def test_url_detection_accepts_http():
    assert _looks_like_url("http://example.com")


def test_url_detection_rejects_search_phrase():
    assert not _looks_like_url("templetwo strongest aspects")


def test_url_detection_rejects_bare_domain():
    """A bare domain without scheme is treated as search — we don't guess
    at whether the user meant a URL or a keyword."""
    assert not _looks_like_url("github.com/templetwo")


def test_url_detection_rejects_empty():
    assert not _looks_like_url("")


def test_url_detection_rejects_non_http_scheme():
    """Only http/https route to /extract. Other schemes route to /search
    (which will likely fail, but that's the search-failure path, not a
    URL-route mistake)."""
    assert not _looks_like_url("ftp://example.com/file.txt")


# --- Search routing -------------------------------------------------------


def _make_client(handler) -> TavilySearchClient:
    transport = httpx.MockTransport(handler)
    return TavilySearchClient(
        api_key="test-key",
        httpx_client=httpx.AsyncClient(transport=transport),
    )


def test_search_query_routes_to_search_endpoint_and_returns_result():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/1",
                        "title": "Result 1",
                        "content": "Snippet text 1.",
                    },
                    {
                        "url": "https://example.com/2",
                        "title": "Result 2",
                        "content": "Snippet text 2.",
                    },
                ],
                "answer": "Combined answer.",
            },
        )

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("templetwo strongest aspects"))

    assert "/search" in captured["path"]
    assert isinstance(out, SearchResult)
    assert out.query == "templetwo strongest aspects"
    assert "Snippet text 1." in out.result_text or "Combined answer." in out.result_text
    assert "https://example.com/1" in out.source_urls
    assert "https://example.com/2" in out.source_urls


def test_url_query_routes_to_extract_endpoint_and_returns_result():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://github.com/templetwo",
                        "raw_content": "Templetwo's README content here.",
                    }
                ],
                "failed_results": [],
            },
        )

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("https://github.com/templetwo"))

    assert "/extract" in captured["path"]
    assert isinstance(out, SearchResult)
    assert out.query == "https://github.com/templetwo"
    assert "Templetwo's README content" in out.result_text
    assert out.source_urls == ("https://github.com/templetwo",)


def test_search_4xx_returns_failed_search():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid api key"})

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("any query"))
    assert isinstance(out, FailedSearch)
    assert "401" in out.reason or "invalid" in out.reason.lower()


def test_search_5xx_returns_failed_search():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("any query"))
    assert isinstance(out, FailedSearch)
    assert "503" in out.reason


def test_extract_with_no_results_returns_failed_search():
    """Tavily /extract can return failed_results when a URL is unreachable.
    The client converts to FailedSearch so peers see the typed failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [],
                "failed_results": [
                    {
                        "url": "https://does-not-exist.example",
                        "error": "404 not found",
                    }
                ],
            },
        )

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("https://does-not-exist.example"))
    assert isinstance(out, FailedSearch)
    assert "404" in out.reason or "not found" in out.reason.lower()


def test_network_exception_returns_failed_search():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("any query"))
    assert isinstance(out, FailedSearch)
    assert "connection" in out.reason.lower() or "refused" in out.reason.lower()


def test_authorization_header_included_on_request():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8") if request.content else ""
        return httpx.Response(200, json={"results": [], "answer": "ok"})

    client = _make_client(handler)
    asyncio.run(client.execute_search("any"))
    # Tavily accepts api_key in the JSON body (per docs) — we verify the key
    # actually got into the request.
    assert "test-key" in captured["body"]


def test_search_result_uses_substrate_entry_id_helper():
    """SearchResult.entry_id must be the content-addressed
    search_result_id(query, executed_at) — substrate enforces this; this
    test verifies the client supplies the canonical id rather than rolling
    its own."""
    from golden_lattice.memory_graph.phase_0 import search_result_id

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": "https://x", "content": "x"}], "answer": ""},
        )

    client = _make_client(handler)
    out = asyncio.run(client.execute_search("q"))
    assert isinstance(out, SearchResult)
    assert isinstance(out.executed_at, datetime)
    assert out.entry_id == search_result_id(out.query, out.executed_at)
