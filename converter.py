"""
Membraine Layer 3: Convert
HTML → clean Markdown using markdownify (Python Turndown.js equivalent).

Uses lxml for robust hidden-element stripping (not regex).
Strips all remaining HTML tags, scripts, styles.
Preserves headings, lists, links, code blocks, tables.
"""

import re
from dataclasses import dataclass

from lxml import etree
from lxml.html import fromstring, tostring


@dataclass
class ConvertResult:
    """Conversion result — clean markdown."""
    markdown: str
    word_count: int
    link_count: int
    hidden_elements_removed: int


# CSS properties that indicate hidden/invisible content
_HIDDEN_CSS_PATTERNS = [
    re.compile(r'display\s*:\s*none', re.IGNORECASE),
    re.compile(r'visibility\s*:\s*hidden', re.IGNORECASE),
    re.compile(r'font-size\s*:\s*0(?:px|em|rem|pt|%)?\s*[;"]?', re.IGNORECASE),
    re.compile(r'opacity\s*:\s*0(?:\.0+)?\s*[;"]?', re.IGNORECASE),
    re.compile(r'color\s*:\s*transparent', re.IGNORECASE),
    # Off-screen positioning (left:-9999px or similar)
    re.compile(r'(?:left|top)\s*:\s*-\d{3,}px', re.IGNORECASE),
    # Height/width: 0
    re.compile(r'(?:height|width)\s*:\s*0(?:px)?\s*[;"]?', re.IGNORECASE),
    # Overflow hidden + tiny size (clip trick)
    re.compile(r'clip\s*:\s*rect\(', re.IGNORECASE),
]

# Tags to strip entirely (including content)
_STRIP_TAGS = {'script', 'style', 'noscript', 'template'}


def _strip_hidden_elements(html: str) -> tuple[str, int]:
    """
    Parse HTML with lxml and remove all hidden/invisible elements.

    Returns (cleaned_html, count_of_removed_elements).
    Much more robust than regex — handles nested tags, attribute ordering,
    different quote styles, multiline content, etc.
    """
    try:
        doc = fromstring(html)
    except Exception:
        # If lxml can't parse it, return as-is
        return html, 0

    removed = 0

    # Collect elements to remove (can't modify tree while iterating)
    to_remove = []

    for el in doc.iter():
        tag = el.tag if isinstance(el.tag, str) else ""

        # Strip dangerous tags entirely
        if tag.lower() in _STRIP_TAGS:
            to_remove.append(el)
            continue

        # Check inline style for hidden patterns
        style = el.get("style", "")
        if style:
            for pattern in _HIDDEN_CSS_PATTERNS:
                if pattern.search(style):
                    to_remove.append(el)
                    break

        # Check aria-hidden
        if (el.get("aria-hidden") or "").lower() == "true":
            to_remove.append(el)
            continue

        # Check data attributes that might carry injection payloads
        # (paranoid mode — strip elements with suspicious data-* attrs)
        for attr_name in el.attrib:
            if attr_name.startswith("data-") and "inject" in attr_name.lower():
                to_remove.append(el)
                break

    # Remove collected elements
    seen = set()
    for el in to_remove:
        if id(el) in seen:
            continue
        seen.add(id(el))
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
            removed += 1

    # Also strip HTML comments from the tree
    for comment in doc.iter(etree.Comment):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
            removed += 1

    # Serialize back to HTML string
    result = tostring(doc, encoding="unicode", method="html")
    return result, removed


def html_to_markdown(
    html: str,
    *,
    strip_images: bool = False,
    include_links: bool = True,
    heading_style: str = "ATX",
) -> ConvertResult:
    """
    Convert article HTML to clean Markdown.

    Args:
        html: Article HTML (from Layer 2 extraction)
        strip_images: If True, remove image references entirely
        include_links: If True, preserve hyperlinks as [text](url)
        heading_style: "ATX" for # headings, "SETEXT" for underline style

    Returns:
        ConvertResult with clean markdown text
    """
    from markdownify import markdownify as md

    # Phase 1: lxml-based removal of hidden elements, comments, scripts
    cleaned_html, hidden_removed = _strip_hidden_elements(html)

    # Phase 2: Convert to markdown
    # markdownify doesn't allow both strip and convert simultaneously
    md_kwargs = dict(
        heading_style=heading_style,
        newline_style="backslash",
        wrap=False,
        wrap_width=0,
    )
    if strip_images:
        md_kwargs['strip'] = ['img']
    if not include_links:
        md_kwargs['convert'] = []

    markdown = md(cleaned_html, **md_kwargs)

    # Phase 3: Post-cleanup
    # Collapse multiple blank lines
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    # Remove trailing whitespace on each line
    markdown = '\n'.join(line.rstrip() for line in markdown.split('\n'))
    # Strip leading/trailing whitespace
    markdown = markdown.strip()

    # Count stats
    words = len(markdown.split())
    links = len(re.findall(r'\[([^\]]+)\]\(([^)]+)\)', markdown))

    return ConvertResult(
        markdown=markdown,
        word_count=words,
        link_count=links,
        hidden_elements_removed=hidden_removed,
    )
