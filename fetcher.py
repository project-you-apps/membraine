"""
Membraine Layer 1: Fetch
Headless Chromium via Playwright — full JS rendering, SPA support.

Usage:
    html = await fetch_page("https://example.com")
"""

import asyncio
from dataclasses import dataclass


@dataclass
class FetchResult:
    """Raw fetch result before any processing."""
    url: str               # final URL after redirects
    html: str              # fully-rendered HTML
    title: str             # page title
    status: int            # HTTP status code
    content_type: str      # MIME type
    fetch_time_ms: float   # wall-clock fetch time
    error: str | None = None


# Default timeout for page load (ms)
PAGE_TIMEOUT = 30_000

# Default wait after network idle (ms) — lets SPAs finish rendering
IDLE_WAIT = 2_000

# User agent — generic modern Chrome to avoid bot detection
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


async def fetch_page(
    url: str,
    *,
    timeout_ms: int = PAGE_TIMEOUT,
    idle_wait_ms: int = IDLE_WAIT,
    browser_context=None,
) -> FetchResult:
    """
    Fetch a URL using headless Chromium and return the rendered HTML.

    If browser_context is provided, uses it (for connection pooling).
    Otherwise creates a fresh browser instance.
    """
    import time
    start = time.monotonic()

    owns_browser = browser_context is None

    try:
        from playwright.async_api import async_playwright

        if owns_browser:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
            )
        else:
            context = browser_context
            pw = None
            browser = None

        page = await context.new_page()

        # Block unnecessary resource types to speed up fetch
        await page.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot}",
                         lambda route: route.abort())

        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

        # Brief wait for any late-loading content
        if idle_wait_ms > 0:
            await page.wait_for_timeout(idle_wait_ms)

        html = await page.content()
        title = await page.title()
        final_url = page.url
        status = response.status if response else 0
        content_type = response.headers.get("content-type", "") if response else ""

        await page.close()

        elapsed = (time.monotonic() - start) * 1000

        if owns_browser:
            await context.close()
            await browser.close()
            await pw.stop()

        return FetchResult(
            url=final_url,
            html=html,
            title=title,
            status=status,
            content_type=content_type,
            fetch_time_ms=round(elapsed, 1),
        )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url,
            html="",
            title="",
            status=0,
            content_type="",
            fetch_time_ms=round(elapsed, 1),
            error=str(e),
        )


class BrowserPool:
    """
    Manages a persistent browser instance for connection reuse.
    Call start() once, then use fetch() repeatedly, then stop().
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self._context = None

    async def start(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            java_script_enabled=True,
        )

    async def fetch(self, url: str, **kwargs) -> FetchResult:
        if self._context is None:
            await self.start()
        return await fetch_page(url, browser_context=self._context, **kwargs)

    async def stop(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._context = None
        self._browser = None
        self._pw = None
