"""
Membraine MCP Server
Secure web fetch for LLM agents — 5-layer defense pipeline.

Transport modes:
  stdio:  python membraine_server.py                    (for Claude Code MCP)
  HTTP:   python membraine_server.py --http --port 8300 (for direct API)

MCP Tools:
  web_fetch        — fetch URL, return ranked semantic chunks
  web_fetch_raw    — fetch URL, return full cleaned markdown
  membraine_status — server health + cache stats
"""

import sys
import os
import json
import asyncio
import argparse
import time
import logging

# Add parent for shared imports
sys.path.insert(0, os.path.dirname(__file__))

from fastmcp import FastMCP
from pipeline import MembrainePipeline
from starlette.requests import Request
from starlette.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("membraine")

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("Membraine")


def _cors_headers() -> dict:
    """CORS headers for browser-extension callers (Heartbeat etc.)."""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, x-client",
    }

pipeline = MembrainePipeline()
_start_time = time.time()

# Pre-warm is deferred to main() so stderr can be redirected first in stdio mode


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def web_fetch(url: str, query: str = "", top_k: int = 5) -> str:
    """
    Fetch a URL through the secure Membraine pipeline and return relevant chunks.

    The page is rendered in headless Chromium, article content extracted,
    converted to markdown, scanned for prompt injection / poison text,
    chunked, embedded, and ranked by semantic similarity to your query.

    Args:
        url: The URL to fetch (must be a valid http/https URL)
        query: Semantic query to rank results. If empty, returns all chunks.
        top_k: Number of top chunks to return (default 5)

    Returns:
        JSON with: title, url, chunks (ranked), threats (transparency), meta (timing)
    """
    log.info(f"web_fetch: {url} | query='{query}' | top_k={top_k}")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = await pipeline.process(url, query=query, top_k=top_k)

    if result.error:
        return json.dumps({"error": result.error, "url": url}, indent=2)

    output = result.to_dict()
    # For the chunked response, omit full markdown to save tokens
    output.pop("markdown", None)

    return json.dumps(output, indent=2, default=str)


@mcp.tool()
async def web_fetch_raw(url: str) -> str:
    """
    Fetch a URL and return the full cleaned markdown (no chunking/embedding).

    Use this when you need the complete page content, not semantic search.
    Faster than web_fetch since it skips embedding.

    Args:
        url: The URL to fetch

    Returns:
        JSON with: title, url, markdown (full text), threats, meta
    """
    log.info(f"web_fetch_raw: {url}")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = await pipeline.process_raw(url)

    if result.error:
        return json.dumps({"error": result.error, "url": url}, indent=2)

    return json.dumps(
        {
            "title": result.title,
            "url": result.url,
            "markdown": result.markdown,
            "threats": [
                {
                    "category": t.category,
                    "name": t.name,
                    "context": t.context,
                    "stripped": t.stripped,
                }
                for t in result.threats
            ],
            "meta": result.meta,
        },
        indent=2,
        default=str,
    )


def _get_status_dict() -> dict:
    """Build status dict — shared by MCP tool and HTTP endpoint."""
    uptime = time.time() - _start_time
    cache = pipeline.cache_stats()
    return {
        "server": "Membraine",
        "version": "0.1.0-practice",
        "uptime_s": round(uptime, 1),
        "browser_pool": "active" if pipeline._started else "stopped",
        "cache": cache,
        "layers": [
            "1: Playwright (headless Chromium)",
            "2: Readability (article extraction)",
            "3: Markdownify (HTML→MD)",
            "4: Poison Guard (adversarial filter)",
            "5: Chunk + Embed (Nomic v1.5)",
        ],
    }


@mcp.tool()
async def membraine_status() -> str:
    """
    Get Membraine server status: uptime, cache stats, browser state.
    """
    return json.dumps(_get_status_dict(), indent=2)


# ---------------------------------------------------------------------------
# HTTP mode (FastAPI mount for direct API access)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REST endpoints — mounted on the same ASGI app as the MCP-SSE transport via
# FastMCP custom_route. Available whether the server runs in --sse or --http
# mode, so Heartbeat's RECEIVE command + boot-report health checks work in
# both cases (same pattern membot uses for /api/filter and /api/search).
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET", "OPTIONS"])
async def http_health(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors_headers())
    return JSONResponse({"status": "ok", "server": "Membraine"}, headers=_cors_headers())


@mcp.custom_route("/status", methods=["GET", "OPTIONS"])
async def http_status(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors_headers())
    return JSONResponse(_get_status_dict(), headers=_cors_headers())


@mcp.custom_route("/fetch", methods=["POST", "OPTIONS"])
async def http_fetch(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors_headers())
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"invalid JSON body: {e}"}, status_code=400, headers=_cors_headers())

    url = data.get("url", "")
    query = data.get("query", "")
    top_k = data.get("top_k", 5)

    if not url:
        return JSONResponse({"error": "url required"}, status_code=400, headers=_cors_headers())
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = await pipeline.process(url, query=query, top_k=top_k)
    return JSONResponse(result.to_dict(), headers=_cors_headers())


@mcp.custom_route("/fetch_raw", methods=["POST", "OPTIONS"])
async def http_fetch_raw(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors_headers())
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"invalid JSON body: {e}"}, status_code=400, headers=_cors_headers())

    url = data.get("url", "")
    if not url:
        return JSONResponse({"error": "url required"}, status_code=400, headers=_cors_headers())
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = await pipeline.process_raw(url)
    return JSONResponse(
        {
            "title": result.title,
            "url": result.url,
            "markdown": result.markdown,
            "threats": [
                {"category": t.category, "name": t.name, "context": t.context}
                for t in result.threats
            ],
            "meta": result.meta,
        },
        headers=_cors_headers(),
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def _shutdown():
    """Clean shutdown — close browser pool."""
    log.info("Shutting down browser pool...")
    await pipeline.stop()
    log.info("Membraine stopped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Membraine — Secure Web Fetch MCP Server"
    )
    parser.add_argument(
        "--http", action="store_true", help="Run in HTTP mode (default: stdio MCP)"
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Run MCP over SSE (for VS Code / remote clients)",
    )
    parser.add_argument(
        "--port", type=int, default=8300, help="HTTP/SSE port (default 8300)"
    )
    parser.add_argument(
        "--cache-ttl", type=int, default=900, help="Cache TTL in seconds (default 900)"
    )
    parser.add_argument(
        "--cache-max", type=int, default=100, help="Max cache entries (default 100)"
    )
    args = parser.parse_args()

    # Configure cache
    pipeline._cache.ttl_seconds = args.cache_ttl
    pipeline._cache.max_entries = args.cache_max

    # Pre-warm the Nomic embedding model
    def _prewarm():
        try:
            from chunker import _get_model

            log.info("Pre-warming Nomic embedding model...")
            _get_model()
            log.info("Nomic model ready.")
        except Exception as e:
            log.warning(f"Model pre-warm failed (will lazy-load on first fetch): {e}")

    if args.http or args.sse:
        # Unified: both modes run the FastMCP app with custom_route REST
        # endpoints mounted alongside the MCP transport. --http uses
        # streamable-http MCP transport, --sse uses SSE. In both cases the
        # REST routes (/health, /status, /fetch, /fetch_raw) are served at
        # their plain paths — /mcp (http) or /sse (sse) is where the MCP
        # protocol lives.
        transport = "streamable-http" if args.http else "sse"
        _prewarm()
        log.info(
            f"Membraine server starting on port {args.port} "
            f"(transport={transport}, REST endpoints mounted: "
            f"/health /status /fetch /fetch_raw)"
        )
        mcp.run(transport=transport, host="0.0.0.0", port=args.port)
    else:
        # stdio mode: redirect ALL stderr to a log file so Claude Code
        # doesn't interpret logging/banners as server errors
        log_path = os.path.join(os.path.dirname(__file__) or ".", "membraine-stdio.log")
        sys.stderr = open(log_path, "a")
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        _prewarm()
        log.info("Membraine MCP server starting (stdio mode)")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
