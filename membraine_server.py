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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("membraine")

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("Membraine")

pipeline = MembrainePipeline()
_start_time = time.time()

# Pre-warm the Nomic embedding model so first web_fetch doesn't take 7 minutes
try:
    from chunker import _get_model
    log.info("Pre-warming Nomic embedding model...")
    _get_model()
    log.info("Nomic model ready.")
except Exception as e:
    log.warning(f"Model pre-warm failed (will lazy-load on first fetch): {e}")


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

    return json.dumps({
        "title": result.title,
        "url": result.url,
        "markdown": result.markdown,
        "threats": [
            {"category": t.category, "name": t.name, "context": t.context, "stripped": t.stripped}
            for t in result.threats
        ],
        "meta": result.meta,
    }, indent=2, default=str)


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

def create_http_app(port: int = 8300):
    """Create FastAPI app with MCP mounted + REST endpoints."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    import uvicorn

    app = FastAPI(title="Membraine", version="0.1.0")

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok", "server": "Membraine"}

    # REST endpoints (bypass MCP for direct HTTP access)
    @app.post("/fetch")
    async def http_fetch(request: dict):
        url = request.get("url", "")
        query = request.get("query", "")
        top_k = request.get("top_k", 5)

        if not url:
            return JSONResponse({"error": "url required"}, status_code=400)

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        result = await pipeline.process(url, query=query, top_k=top_k)
        return result.to_dict()

    @app.post("/fetch_raw")
    async def http_fetch_raw(request: dict):
        url = request.get("url", "")
        if not url:
            return JSONResponse({"error": "url required"}, status_code=400)

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        result = await pipeline.process_raw(url)
        return {
            "title": result.title,
            "url": result.url,
            "markdown": result.markdown,
            "threats": [
                {"category": t.category, "name": t.name, "context": t.context}
                for t in result.threats
            ],
            "meta": result.meta,
        }

    @app.get("/status")
    async def http_status():
        return _get_status_dict()

    return app, port


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
    parser = argparse.ArgumentParser(description="Membraine — Secure Web Fetch MCP Server")
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode (default: stdio MCP)")
    parser.add_argument("--port", type=int, default=8300, help="HTTP port (default 8300)")
    parser.add_argument("--cache-ttl", type=int, default=900, help="Cache TTL in seconds (default 900)")
    parser.add_argument("--cache-max", type=int, default=100, help="Max cache entries (default 100)")
    args = parser.parse_args()

    # Configure cache
    pipeline._cache.ttl_seconds = args.cache_ttl
    pipeline._cache.max_entries = args.cache_max

    if args.http:
        import uvicorn
        app, port = create_http_app(args.port)
        log.info(f"Membraine HTTP server starting on port {port}")
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        log.info("Membraine MCP server starting (stdio mode)")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
