"""
Membraine Layer 4: Poison Guard
Detects and strips adversarial content targeting LLM agents.

Defense layers:
1. Zero-width and invisible Unicode removal
2. Instruction/prompt injection pattern detection
3. Homoglyph normalization
4. Suspicious whitespace cleanup
5. Base64 payload detection
6. HTML entity remnant cleanup

Returns cleaned text + transparency report of what was caught.
"""

import json
import os
import re
import base64
import unicodedata
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Zero-width and invisible characters
# ---------------------------------------------------------------------------

ZERO_WIDTH_CHARS = {
    '\u200b',  # ZERO WIDTH SPACE
    '\u200c',  # ZERO WIDTH NON-JOINER
    '\u200d',  # ZERO WIDTH JOINER
    '\u200e',  # LEFT-TO-RIGHT MARK
    '\u200f',  # RIGHT-TO-LEFT MARK
    '\u2060',  # WORD JOINER
    '\u2061',  # FUNCTION APPLICATION
    '\u2062',  # INVISIBLE TIMES
    '\u2063',  # INVISIBLE SEPARATOR
    '\u2064',  # INVISIBLE PLUS
    '\ufeff',  # ZERO WIDTH NO-BREAK SPACE (BOM)
    '\u00ad',  # SOFT HYPHEN
    '\u034f',  # COMBINING GRAPHEME JOINER
    '\u061c',  # ARABIC LETTER MARK
    '\u180e',  # MONGOLIAN VOWEL SEPARATOR
    '\ufff9',  # INTERLINEAR ANNOTATION ANCHOR
    '\ufffa',  # INTERLINEAR ANNOTATION SEPARATOR
    '\ufffb',  # INTERLINEAR ANNOTATION TERMINATOR
}

# Regex matching any zero-width char
ZW_PATTERN = re.compile('[' + ''.join(re.escape(c) for c in ZERO_WIDTH_CHARS) + ']+')

# Tag-variation Unicode (e.g. U+E0001..U+E007F — language tags used for steganography)
TAG_RANGE = re.compile('[\U000e0001-\U000e007f]+')


# ---------------------------------------------------------------------------
# Instruction / prompt injection patterns — loaded from signatures.json
# ---------------------------------------------------------------------------

_SIGNATURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signatures.json')


def _load_signatures(path: str = _SIGNATURES_PATH) -> list[tuple[str, str]]:
    """Load injection patterns from signatures.json. Falls back to empty list."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return [(sig['pattern'], sig['name']) for sig in data.get('signatures', [])]
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"WARNING: Could not load signatures from {path}: {e}")
        return []


INJECTION_PATTERNS = _load_signatures()

# Compile all patterns
COMPILED_INJECTIONS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), name)
    for pattern, name in INJECTION_PATTERNS
]


# ---------------------------------------------------------------------------
# Homoglyph normalization (common confusables)
# ---------------------------------------------------------------------------

# Cyrillic/Greek letters that look like Latin — used to bypass keyword filters
HOMOGLYPH_MAP = {
    '\u0410': 'A', '\u0412': 'B', '\u0421': 'C', '\u0415': 'E',
    '\u041d': 'H', '\u0406': 'I', '\u041a': 'K', '\u041c': 'M',
    '\u041e': 'O', '\u0420': 'P', '\u0422': 'T', '\u0425': 'X',
    '\u0430': 'a', '\u0435': 'e', '\u043e': 'o', '\u0440': 'p',
    '\u0441': 'c', '\u0443': 'u', '\u0445': 'x', '\u0443': 'y',
    '\u0456': 'i',
    # Greek
    '\u0391': 'A', '\u0392': 'B', '\u0395': 'E', '\u0397': 'H',
    '\u0399': 'I', '\u039a': 'K', '\u039c': 'M', '\u039d': 'N',
    '\u039f': 'O', '\u03a1': 'P', '\u03a4': 'T', '\u03a7': 'X',
    '\u03b1': 'a', '\u03b5': 'e', '\u03bf': 'o', '\u03c1': 'p',
    # Fullwidth Latin
    '\uff21': 'A', '\uff22': 'B', '\uff23': 'C', '\uff24': 'D',
    '\uff25': 'E', '\uff26': 'F', '\uff27': 'G', '\uff28': 'H',
    '\uff29': 'I', '\uff2a': 'J', '\uff2b': 'K', '\uff2c': 'L',
    '\uff2d': 'M', '\uff2e': 'N', '\uff2f': 'O', '\uff30': 'P',
    '\uff31': 'Q', '\uff32': 'R', '\uff33': 'S', '\uff34': 'T',
    '\uff35': 'U', '\uff36': 'V', '\uff37': 'W', '\uff38': 'X',
    '\uff39': 'Y', '\uff3a': 'Z',
    '\uff41': 'a', '\uff42': 'b', '\uff43': 'c', '\uff44': 'd',
    '\uff45': 'e', '\uff46': 'f', '\uff47': 'g', '\uff48': 'h',
    '\uff49': 'i', '\uff4a': 'j', '\uff4b': 'k', '\uff4c': 'l',
    '\uff4d': 'm', '\uff4e': 'n', '\uff4f': 'o', '\uff50': 'p',
    '\uff51': 'q', '\uff52': 'r', '\uff53': 's', '\uff54': 't',
    '\uff55': 'u', '\uff56': 'v', '\uff57': 'w', '\uff58': 'x',
    '\uff59': 'y', '\uff5a': 'z',
}

HOMOGLYPH_PATTERN = re.compile(
    '[' + ''.join(re.escape(c) for c in HOMOGLYPH_MAP.keys()) + ']'
)


# ---------------------------------------------------------------------------
# Suspicious whitespace / formatting
# ---------------------------------------------------------------------------

# Excessive blank lines (>3 consecutive) — sometimes used to push content off-screen
EXCESSIVE_BLANKS = re.compile(r'\n{4,}')

# Tab floods
TAB_FLOOD = re.compile(r'\t{10,}')

# Extremely long lines with no spaces (possible encoded payload)
LONG_NOSPACE = re.compile(r'[^\s]{500,}')


# ---------------------------------------------------------------------------
# Base64 payload detection
# ---------------------------------------------------------------------------

# Matches strings that look like base64 (at least 40 chars, valid alphabet)
B64_CANDIDATE = re.compile(r'[A-Za-z0-9+/=]{40,}')


# ---------------------------------------------------------------------------
# HTML entity remnants (post-markdown conversion leftovers)
# ---------------------------------------------------------------------------

HTML_ENTITIES = re.compile(r'&(?:#\d{1,5}|#x[0-9a-fA-F]{1,4}|[a-zA-Z]+);')


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Threat:
    """A detected threat in the content."""
    category: str          # e.g. 'zero_width', 'injection', 'homoglyph'
    name: str              # specific threat name
    context: str           # snippet showing where it was found
    line_number: int = 0   # approximate line number
    stripped: bool = True   # whether it was removed


@dataclass
class GuardResult:
    """Result of running the poison guard."""
    cleaned_text: str
    threats: list = field(default_factory=list)
    original_length: int = 0
    cleaned_length: int = 0
    homoglyphs_normalized: int = 0
    zero_width_removed: int = 0
    injections_found: int = 0

    @property
    def is_clean(self) -> bool:
        return len(self.threats) == 0

    def summary(self) -> str:
        if self.is_clean:
            return "Clean: no threats detected"
        cats = {}
        for t in self.threats:
            cats[t.category] = cats.get(t.category, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(cats.items())]
        return f"Threats: {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Main guard function
# ---------------------------------------------------------------------------

def guard(text: str, *, strip_injections: bool = True) -> GuardResult:
    """
    Run the full poison guard pipeline on text.

    Args:
        text: Input text (typically markdown from Layer 3)
        strip_injections: If True, remove detected injection lines.
                         If False, flag but preserve them.

    Returns:
        GuardResult with cleaned text and threat report.
    """
    threats = []
    original_length = len(text)
    cleaned = text

    # --- Pass 1: Zero-width characters ---
    # Replace with space (not empty string) to prevent word concatenation.
    # e.g. "He\u200bloved" → "He loved" not "Heloved"
    # Excess spaces are collapsed in Pass 8.
    zw_count = 0
    def _count_zw(m):
        nonlocal zw_count
        zw_count += len(m.group())
        return ' '

    cleaned = ZW_PATTERN.sub(_count_zw, cleaned)
    cleaned = TAG_RANGE.sub(' ', cleaned)

    if zw_count > 0:
        threats.append(Threat(
            category='zero_width',
            name='invisible_chars',
            context=f'{zw_count} zero-width characters removed',
        ))

    # --- Pass 2: Homoglyph normalization ---
    glyph_count = 0
    def _normalize_glyph(m):
        nonlocal glyph_count
        glyph_count += 1
        return HOMOGLYPH_MAP.get(m.group(), m.group())

    cleaned = HOMOGLYPH_PATTERN.sub(_normalize_glyph, cleaned)

    if glyph_count > 0:
        threats.append(Threat(
            category='homoglyph',
            name='confusable_chars',
            context=f'{glyph_count} homoglyphs normalized to Latin',
        ))

    # --- Pass 3: Injection pattern detection ---
    injection_count = 0
    lines = cleaned.split('\n')
    flagged_lines = set()

    for line_idx, line in enumerate(lines):
        for pattern, name in COMPILED_INJECTIONS:
            if pattern.search(line):
                injection_count += 1
                ctx = line[:120].strip()
                threats.append(Threat(
                    category='injection',
                    name=name,
                    context=ctx,
                    line_number=line_idx + 1,
                    stripped=strip_injections,
                ))
                if strip_injections:
                    flagged_lines.add(line_idx)

    if strip_injections and flagged_lines:
        lines = [l for i, l in enumerate(lines) if i not in flagged_lines]
        cleaned = '\n'.join(lines)

    # --- Pass 4: Suspicious whitespace ---
    if EXCESSIVE_BLANKS.search(cleaned):
        cleaned = EXCESSIVE_BLANKS.sub('\n\n\n', cleaned)
        threats.append(Threat(
            category='whitespace',
            name='excessive_blanks',
            context='Collapsed >3 consecutive blank lines',
        ))

    if TAB_FLOOD.search(cleaned):
        cleaned = TAB_FLOOD.sub('\t', cleaned)
        threats.append(Threat(
            category='whitespace',
            name='tab_flood',
            context='Collapsed tab floods',
        ))

    # --- Pass 5: Base64 payload detection ---
    for m in B64_CANDIDATE.finditer(cleaned):
        candidate = m.group()
        try:
            decoded = base64.b64decode(candidate, validate=True)
            # Check if it decodes to readable text (likely payload)
            decoded_text = decoded.decode('utf-8', errors='strict')
            if len(decoded_text) > 20 and any(c.isalpha() for c in decoded_text):
                threats.append(Threat(
                    category='encoded_payload',
                    name='base64_text',
                    context=f'Base64 decodes to: {decoded_text[:80]}...',
                    stripped=False,  # Flag but don't strip — could be legit code
                ))
        except (ValueError, UnicodeDecodeError):
            pass  # Not valid base64 or not UTF-8 text — harmless

    # --- Pass 6: Long no-space strings (possible obfuscation) ---
    for m in LONG_NOSPACE.finditer(cleaned):
        # Skip if it looks like a URL or code
        snippet = m.group()[:80]
        if snippet.startswith(('http://', 'https://', 'data:', 'base64')):
            continue
        threats.append(Threat(
            category='obfuscation',
            name='long_nospace_string',
            context=f'Suspicious {len(m.group())}-char unbroken string: {snippet}...',
            stripped=False,
        ))

    # --- Pass 7: HTML entity remnants ---
    entity_count = len(HTML_ENTITIES.findall(cleaned))
    if entity_count > 20:  # A few are normal; many suggest incomplete stripping
        threats.append(Threat(
            category='html_remnant',
            name='entity_flood',
            context=f'{entity_count} HTML entities remaining (possible incomplete conversion)',
            stripped=False,
        ))

    # --- Pass 8: ASCII normalization (scorched earth) ---
    # Decompose accented characters to base + combining mark, then strip marks.
    # café → cafe, naïve → naive, über → uber. Covers all Latin-script languages.
    cleaned = unicodedata.normalize('NFKD', cleaned)
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c) != 'Mn')

    # Keep only printable ASCII (32-126) plus newline and tab.
    # Everything else becomes a space. No invisible chars, no homoglyphs,
    # no Unicode trickery of any kind can survive this.
    pre_ascii_len = len(cleaned)
    cleaned = ''.join(c if (32 <= ord(c) <= 126 or c in '\n\t') else ' ' for c in cleaned)
    non_ascii_replaced = pre_ascii_len - sum(1 for c in cleaned if c != ' ') - cleaned.count(' ')

    # Collapse any runs of spaces created by replacements
    cleaned = re.sub(r'  +', ' ', cleaned)

    # --- Final cleanup: normalize line endings ---
    cleaned = cleaned.replace('\r\n', '\n').replace('\r', '\n')
    cleaned = cleaned.strip()

    return GuardResult(
        cleaned_text=cleaned,
        threats=threats,
        original_length=original_length,
        cleaned_length=len(cleaned),
        homoglyphs_normalized=glyph_count,
        zero_width_removed=zw_count,
        injections_found=injection_count,
    )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def is_safe(text: str) -> bool:
    """Quick check: does the text pass without any threats?"""
    return guard(text).is_clean


def clean(text: str) -> str:
    """Just return the cleaned text, discarding threat report."""
    return guard(text).cleaned_text
