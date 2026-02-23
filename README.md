# Membraine

**Secure web fetch for LLM agents.**

A hardened MCP server that fetches web pages through a 5-layer defense pipeline, strips adversarial content, and returns clean semantic chunks. Drop it into any MCP-compatible agent to protect against prompt injection via web content.

Every AI agent that browses the web is one poisoned page away from prompt injection. Membraine is the membrane between your brain and the wild web.

## The Problem

Web pages can contain adversarial content that targets LLM agents:

- **Hidden text** via CSS (`display:none`, `visibility:hidden`, `font-size:0`, `opacity:0`, `color:transparent`, off-screen positioning)
- **HTML comments** with injection payloads
- **Zero-width Unicode characters** encoding hidden instructions between visible words
- **Format injection tokens** (`[INST]`, `<<SYS>>`, `<|im_start|>`, `Human:`, `system:`)
- **Homoglyph attacks** using Cyrillic/Greek/fullwidth characters that look Latin but bypass filters
- **Base64-encoded payloads** hidden in plain sight

No single sanitization step catches everything. Membraine uses defense-in-depth: five independent layers, each reducing the attack surface.

## Architecture

```
URL
 |
 v
+-----------------------------------+
| Layer 1: FETCH (Playwright)       |  Headless Chromium, full JS render
| -> raw HTML after JS execution    |
+----------------+------------------+
                 |
                 v
+-----------------------------------+
| Layer 1.5: PRE-STRIP (lxml)      |  Remove CSS-hidden elements BEFORE
| -> sanitized HTML                 |  Readability strips style attributes
+----------------+------------------+
                 |
                 v
+-----------------------------------+
| Layer 2: EXTRACT (Readability)    |  Mozilla's article extractor
| -> article HTML only              |  Strips nav, ads, sidebars, chrome
+----------------+------------------+
                 |
                 v
+-----------------------------------+
| Layer 3: CONVERT (markdownify)    |  HTML -> clean Markdown
| -> markdown text                  |  Strips all remaining tags/scripts
+----------------+------------------+
                 |
                 v
+-----------------------------------+
| Layer 4: POISON GUARD             |  8-pass adversarial filter
| -> cleaned ASCII text             |  Zero-width, homoglyphs, injections,
|                                   |  base64, whitespace, ASCII normalize
+----------------+------------------+
                 |
                 v
+-----------------------------------+
| Layer 5: CHUNK + EMBED            |  Sentence-aware splitting
| -> semantic chunks with vectors   |  Nomic embeddings, cosine ranking
+-----------------------------------+
```

## The Poison Guard

The core differentiator. Eight independent defense passes with updatable threat signatures:

| Pass | Defense | What It Catches |
|------|---------|-----------------|
| 1 | Zero-width removal | U+200B, U+200C, U+200D, U+FEFF, U+2060, tag chars, 17 char classes |
| 2 | Homoglyph normalization | Cyrillic, Greek, fullwidth Latin -> ASCII equivalents |
| 3 | Injection pattern detection | 31 regex signatures loaded from `signatures.json` |
| 4 | Whitespace sanitization | Tab floods, excessive blank lines, off-screen pushes |
| 5 | Base64 payload detection | Decoded text payloads flagged (binary ignored) |
| 6 | Long string detection | 500+ char unbroken strings (possible obfuscation) |
| 7 | HTML entity remnants | Flood of entities suggests incomplete conversion |
| 8 | ASCII normalization | NFKD decompose, strip combining marks, printable ASCII only |

Threat signatures are loaded from [`signatures.json`](signatures.json) -- add new patterns without touching code.

Every threat is reported in a transparency log so your agent knows exactly what was caught and stripped.

## Tested Against

Here's what Membraine catches in our adversarial test page -- a chocolate chip cookie recipe hiding 9 injection vectors:

```
+-----------------------------------------------+-------------------+
| Attack Vector                                  | Result            |
+-----------------------------------------------+-------------------+
| CSS display:none div (pirate chef persona)     | STRIPPED (Lyr 1.5)|
| CSS font-size:0 (47 cups cayenne + hamster)    | STRIPPED (Lyr 1.5)|
| CSS visibility:hidden (Gordon Ramsay rant)     | STRIPPED (Lyr 1.5)|
| aria-hidden (uranium-235 cookies / INTERPOL)   | STRIPPED (Lyr 1.5)|
| CSS opacity:0 (Henry VIII weapon of war)       | STRIPPED (Lyr 1.5)|
| Off-screen positioning (anchovy / motor oil)   | STRIPPED (Lyr 1.5)|
| Zero-width chars between words                 | STRIPPED (Pass 1) |
| [INST] DAN jailbreak prompt                    | STRIPPED (Pass 3) |
| <<SYS>> override + Human:/Assistant: roles     | STRIPPED (Pass 3) |
| HTML comment with injection keywords           | STRIPPED (Lyr 1.5)|
+-----------------------------------------------+-------------------+
```

Output: one clean cookie recipe. No hamster. No uranium. No INTERPOL.

> "Add 47 cups of cayenne pepper and one live hamster"
> -- what your agent would have read without Membraine

## Quick Start

### As an MCP Server (Claude Code)

```json
{
  "mcpServers": {
    "membraine": {
      "command": "python",
      "args": ["path/to/membraine_server.py"],
      "type": "stdio"
    }
  }
}
```

Then from Claude Code:
```
"Fetch https://example.com and summarize the main points"
```

### As an HTTP API

```bash
python membraine_server.py --http --port 8300
```

```bash
curl -X POST http://localhost:8300/fetch \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "query": "main points"}'
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `web_fetch` | Fetch URL, return ranked semantic chunks by query |
| `web_fetch_raw` | Fetch URL, return full cleaned markdown |
| `membraine_status` | Server health, cache stats, browser state |

## Dependencies

```
playwright>=1.40
readability-lxml>=0.8
markdownify>=0.12
sentence-transformers>=2.2
fastmcp>=0.1
numpy
lxml
cssselect
uvicorn
fastapi
```

After install: `playwright install chromium`

## File Structure

```
membraine/
  membraine_server.py    # FastMCP server + HTTP routes
  fetcher.py             # Layer 1: Playwright browser pool
  extractor.py           # Layer 2: Readability article extraction
  converter.py           # Layer 3: HTML -> Markdown + hidden element stripping
  poison_guard.py        # Layer 4: 8-pass adversarial filter
  chunker.py             # Layer 5: Sentence-aware chunking + Nomic embeddings
  pipeline.py            # Orchestrates all layers
  cache.py               # URL -> result LRU cache (15-min TTL)
  signatures.json        # Updatable threat signature definitions
  test_poison_guard.py   # 27 adversarial test cases
  test_poison.html       # Adversarial test page (the cookie recipe)
  requirements.txt       # Python dependencies
```

## Running Tests

```bash
python test_poison_guard.py
```

27 tests covering: zero-width chars, homoglyphs, ChatML injection, [INST] tags, <<SYS>> tags, role hijacking, persona override, prompt leaking, forced output, base64 payloads, whitespace attacks, combined multi-vector attacks, and clean text pass-through.

## How It Works (The Key Insight)

Most web sanitizers strip hidden elements *after* article extraction. But Readability (Layer 2) strips `style` attributes while keeping the text content of hidden elements. So `<div style="display:none">evil text</div>` becomes just `evil text` -- invisible to any post-extraction CSS check.

Membraine runs hidden element stripping *before* Readability (Layer 1.5), while the style attributes are still intact. Defense in depth: the same check runs again after conversion (Layer 3) to catch anything Readability might introduce.

## License

MIT

## Credits

Built by [Waving Cat Learning Systems](https://github.com/project-you-apps) as part of the Project-You neuromorphic memory platform.

Uses: [Playwright](https://playwright.dev/), [readability-lxml](https://github.com/buriy/python-readability), [markdownify](https://github.com/matthewwithanm/python-markdownify), [Nomic Embed](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5), [FastMCP](https://github.com/jlowin/fastmcp).
