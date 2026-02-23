"""
Membraine Pipeline
Orchestrates Layers 1-5 into a single fetch-and-process flow.

    URL → fetch → extract → convert → guard → chunk+embed → results
"""

import time
from dataclasses import dataclass, field

from fetcher import fetch_page, FetchResult, BrowserPool
from extractor import extract_with_fallback, ExtractResult
from converter import html_to_markdown, ConvertResult
from poison_guard import guard, GuardResult
from chunker import chunk_text, embed_chunks, rank_chunks, Chunk
from cache import MembraineCache, CacheEntry


@dataclass
class PipelineResult:
    """Full result from the Membraine pipeline."""
    url: str
    title: str
    markdown: str               # full cleaned markdown
    chunks: list                # all chunks with embeddings
    ranked_chunks: list         # top-K chunks (if query provided)
    threats: list               # detected threats
    meta: dict = field(default_factory=dict)
    error: str | None = None
    cached: bool = False

    def to_dict(self) -> dict:
        """Serialize for MCP response."""
        return {
            "url": self.url,
            "title": self.title,
            "markdown": self.markdown if not self.ranked_chunks else None,
            "chunks": [
                {
                    "text": c.text,
                    "score": round(c.score, 4),
                    "index": c.index,
                    "word_count": c.word_count,
                }
                for c in (self.ranked_chunks or self.chunks)
            ],
            "threats": [
                {
                    "category": t.category,
                    "name": t.name,
                    "context": t.context,
                    "stripped": t.stripped,
                }
                for t in self.threats
            ],
            "meta": self.meta,
            "cached": self.cached,
            "error": self.error,
        }


class MembrainePipeline:
    """
    The full Membraine pipeline: fetch → extract → convert → guard → chunk → embed.

    Usage:
        pipeline = MembrainePipeline()
        await pipeline.start()

        result = await pipeline.process("https://example.com", query="what is this about?")
        print(result.ranked_chunks)

        await pipeline.stop()
    """

    def __init__(self, *, cache_ttl: int = 900, cache_max: int = 100):
        self._pool = BrowserPool()
        self._cache = MembraineCache(max_entries=cache_max, ttl_seconds=cache_ttl)
        self._started = False

    async def start(self):
        """Initialize browser pool."""
        await self._pool.start()
        self._started = True

    async def stop(self):
        """Shut down browser pool."""
        await self._pool.stop()
        self._started = False

    async def process(
        self,
        url: str,
        *,
        query: str = "",
        top_k: int = 5,
        skip_cache: bool = False,
        strip_images: bool = False,
    ) -> PipelineResult:
        """
        Full pipeline: URL → clean chunks ranked by query.

        Args:
            url: URL to fetch
            query: Semantic query for ranking chunks (empty = return all)
            top_k: Number of chunks to return
            skip_cache: Force re-fetch even if cached
            strip_images: Remove image references from markdown

        Returns:
            PipelineResult with cleaned content and threat report
        """
        t_start = time.monotonic()

        # --- Check cache ---
        if not skip_cache:
            cached = self._cache.get(url)
            if cached:
                # Re-rank with new query if provided
                ranked = []
                if query and cached.chunks:
                    ranked = rank_chunks(cached.chunks, query, top_k=top_k)

                return PipelineResult(
                    url=cached.url,
                    title=cached.title,
                    markdown=cached.markdown,
                    chunks=cached.chunks,
                    ranked_chunks=ranked,
                    threats=cached.threats,
                    meta={**cached.meta, "from_cache": True},
                    cached=True,
                )

        # --- Layer 1: Fetch ---
        if not self._started:
            await self.start()

        t_fetch_start = time.monotonic()
        fetch_result = await self._pool.fetch(url)
        t_fetch = (time.monotonic() - t_fetch_start) * 1000

        if fetch_result.error:
            return PipelineResult(
                url=url,
                title="",
                markdown="",
                chunks=[],
                ranked_chunks=[],
                threats=[],
                meta={"fetch_time_ms": round(t_fetch, 1)},
                error=f"Fetch failed: {fetch_result.error}",
            )

        if not fetch_result.html:
            return PipelineResult(
                url=fetch_result.url,
                title=fetch_result.title,
                markdown="",
                chunks=[],
                ranked_chunks=[],
                threats=[],
                meta={"fetch_time_ms": round(t_fetch, 1), "status": fetch_result.status},
                error="Empty page content",
            )

        # --- Layer 1.5: Pre-strip hidden elements from raw HTML ---
        # Must happen BEFORE Readability, which strips style attributes
        # but keeps the text content of hidden elements.
        from converter import _strip_hidden_elements
        sanitized_html, pre_hidden_removed = _strip_hidden_elements(fetch_result.html)

        # --- Layer 2: Extract ---
        t_extract_start = time.monotonic()
        extract_result = extract_with_fallback(sanitized_html, url=fetch_result.url)
        t_extract = (time.monotonic() - t_extract_start) * 1000

        # --- Layer 3: Convert ---
        t_convert_start = time.monotonic()
        convert_result = html_to_markdown(
            extract_result.html,
            strip_images=strip_images,
        )
        t_convert = (time.monotonic() - t_convert_start) * 1000

        # --- Layer 4: Guard ---
        t_guard_start = time.monotonic()
        guard_result = guard(convert_result.markdown)
        t_guard = (time.monotonic() - t_guard_start) * 1000

        cleaned_markdown = guard_result.cleaned_text
        title = extract_result.title or fetch_result.title

        # --- Layer 5: Chunk + Embed ---
        t_chunk_start = time.monotonic()
        chunks = chunk_text(cleaned_markdown)
        chunks = embed_chunks(chunks)
        t_chunk = (time.monotonic() - t_chunk_start) * 1000

        # Rank if query provided
        ranked = []
        if query and chunks:
            ranked = rank_chunks(chunks, query, top_k=top_k)

        t_total = (time.monotonic() - t_start) * 1000

        meta = {
            "status": fetch_result.status,
            "word_count": convert_result.word_count,
            "chunk_count": len(chunks),
            "fetch_time_ms": round(t_fetch, 1),
            "extract_time_ms": round(t_extract, 1),
            "convert_time_ms": round(t_convert, 1),
            "guard_time_ms": round(t_guard, 1),
            "chunk_embed_time_ms": round(t_chunk, 1),
            "total_time_ms": round(t_total, 1),
            "layers_applied": ["fetch", "extract", "convert", "guard", "chunk_embed"],
            "threats_found": len(guard_result.threats),
            "hidden_elements_removed": pre_hidden_removed + convert_result.hidden_elements_removed,
            "zero_width_removed": guard_result.zero_width_removed,
            "homoglyphs_normalized": guard_result.homoglyphs_normalized,
            "injections_stripped": guard_result.injections_found,
        }

        # --- Cache the processed result ---
        self._cache.put(url, CacheEntry(
            url=fetch_result.url,
            title=title,
            markdown=cleaned_markdown,
            chunks=chunks,
            threats=guard_result.threats,
            meta=meta,
        ))

        return PipelineResult(
            url=fetch_result.url,
            title=title,
            markdown=cleaned_markdown,
            chunks=chunks,
            ranked_chunks=ranked,
            threats=guard_result.threats,
            meta=meta,
        )

    async def process_raw(
        self,
        url: str,
        *,
        skip_cache: bool = False,
        strip_images: bool = False,
    ) -> PipelineResult:
        """
        Pipeline without chunking/embedding — returns full cleaned markdown.
        Useful when you need the complete page, not semantic search.
        """
        result = await self.process(
            url,
            query="",
            skip_cache=skip_cache,
            strip_images=strip_images,
        )
        # Clear chunks from result for raw mode
        result.ranked_chunks = []
        return result

    def cache_stats(self) -> dict:
        return self._cache.stats()
