"""
Membraine Layer 2: Extract
Uses readability-lxml (Python port of Mozilla Readability.js)
to extract article content from rendered HTML.

Strips navigation, ads, sidebars, footers, popups.
Falls back to full body if no article detected.
"""

from dataclasses import dataclass


@dataclass
class ExtractResult:
    """Extraction result — clean article HTML."""
    html: str          # article HTML (simplified, no nav/ads)
    title: str         # extracted title (may differ from page title)
    short_title: str   # condensed title
    content_length: int


def extract_article(html: str, url: str = "") -> ExtractResult:
    """
    Extract article content from full-page HTML.

    Uses readability-lxml which mirrors Mozilla's Readability.js algorithm:
    - Scores DOM nodes by text density, paragraph count, link density
    - Identifies the main content container
    - Strips everything else (nav, sidebar, footer, ads, comments)

    Args:
        html: Full rendered HTML string
        url: Original URL (used for resolving relative links)

    Returns:
        ExtractResult with clean article HTML
    """
    from readability import Document

    doc = Document(html, url=url)

    article_html = doc.summary()
    title = doc.title() or ""
    short_title = doc.short_title() or title

    return ExtractResult(
        html=article_html,
        title=title,
        short_title=short_title,
        content_length=len(article_html),
    )


def extract_with_fallback(html: str, url: str = "") -> ExtractResult:
    """
    Try Readability extraction; if it produces too little content,
    fall back to stripping scripts/styles from the full body.
    """
    try:
        result = extract_article(html, url)
        # If Readability produced meaningful content, use it
        if result.content_length > 200:
            return result
    except Exception:
        pass

    # Fallback: strip dangerous tags, keep body content
    from lxml.html import fromstring, tostring
    from lxml.html.clean import Cleaner

    cleaner = Cleaner(
        scripts=True,
        javascript=True,
        comments=True,
        style=True,
        links=True,        # <link> tags (not <a>)
        meta=True,
        page_structure=False,
        processing_instructions=True,
        embedded=True,
        frames=True,
        forms=True,
        annoying_tags=True,
        remove_tags=["noscript", "iframe", "object", "embed", "applet"],
        remove_unknown_tags=False,
        safe_attrs_only=True,
    )

    try:
        doc = fromstring(html)
        cleaned = cleaner.clean_html(doc)
        body = cleaned.find('.//body')
        if body is not None:
            fallback_html = tostring(body, encoding='unicode', method='html')
        else:
            fallback_html = tostring(cleaned, encoding='unicode', method='html')
    except Exception:
        # Last resort: return raw HTML (downstream layers will handle)
        fallback_html = html

    return ExtractResult(
        html=fallback_html,
        title="(fallback extraction)",
        short_title="(fallback)",
        content_length=len(fallback_html),
    )
