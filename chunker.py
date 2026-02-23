"""
Membraine Layer 5: Chunk + Embed
Splits cleaned markdown into semantic chunks and embeds with Nomic.

Chunking destroys any instruction structure that survived earlier layers —
an injection spanning multiple sentences gets split across chunks,
and only the relevant chunks (by query similarity) reach the LLM.
"""

import re
import numpy as np
from dataclasses import dataclass


@dataclass
class Chunk:
    """A single text chunk with optional embedding."""
    text: str
    index: int              # position in original document
    char_offset: int        # character offset in original text
    word_count: int
    embedding: np.ndarray | None = None
    score: float = 0.0      # populated at query time


# ---------------------------------------------------------------------------
# Sentence-aware chunking
# ---------------------------------------------------------------------------

# Sentence boundary: period/question/exclamation followed by space or newline
SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z\u201c"\(])')

# Paragraph boundary
PARAGRAPH_BOUNDARY = re.compile(r'\n\s*\n')


def chunk_text(
    text: str,
    *,
    target_tokens: int = 400,
    overlap_tokens: int = 50,
    chars_per_token: float = 4.0,
) -> list[Chunk]:
    """
    Split text into overlapping chunks, respecting sentence boundaries.

    Strategy:
    1. Split into paragraphs
    2. Split paragraphs into sentences
    3. Accumulate sentences until target size reached
    4. Add overlap from previous chunk's tail

    Args:
        target_tokens: Target chunk size in approximate tokens
        overlap_tokens: Overlap with previous chunk
        chars_per_token: Approximate characters per token (for Nomic)

    Returns:
        List of Chunk objects (without embeddings)
    """
    if not text.strip():
        return []

    target_chars = int(target_tokens * chars_per_token)
    overlap_chars = int(overlap_tokens * chars_per_token)

    # Split into paragraphs, then sentences
    paragraphs = PARAGRAPH_BOUNDARY.split(text)
    sentences = []
    for para in paragraphs:
        para_sentences = SENTENCE_BOUNDARY.split(para.strip())
        sentences.extend(para_sentences)
        sentences.append("")  # paragraph separator

    # Accumulate sentences into chunks
    chunks = []
    current_text = ""
    current_offset = 0
    char_pos = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            if current_text:
                current_text += "\n\n"
            continue

        candidate = current_text + (" " if current_text else "") + sent

        if len(candidate) > target_chars and current_text:
            # Flush current chunk
            chunks.append(Chunk(
                text=current_text.strip(),
                index=len(chunks),
                char_offset=current_offset,
                word_count=len(current_text.split()),
            ))

            # Start new chunk with overlap
            if overlap_chars > 0 and len(current_text) > overlap_chars:
                # Take tail of previous chunk as overlap
                overlap = current_text[-overlap_chars:]
                # Try to start at a word boundary
                space_idx = overlap.find(' ')
                if space_idx > 0:
                    overlap = overlap[space_idx + 1:]
                current_text = overlap + " " + sent
            else:
                current_text = sent

            current_offset = char_pos
        else:
            if not current_text:
                current_offset = char_pos
            current_text = candidate

        char_pos += len(sent) + 1

    # Don't forget the last chunk
    if current_text.strip():
        chunks.append(Chunk(
            text=current_text.strip(),
            index=len(chunks),
            char_offset=current_offset,
            word_count=len(current_text.split()),
        ))

    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

_model = None


def _get_model():
    """Lazy-load the Nomic embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5",
            trust_remote_code=True,
        )
    return _model


def embed_chunks(chunks: list[Chunk], *, prefix: str = "search_document: ") -> list[Chunk]:
    """
    Embed all chunks using Nomic nomic-embed-text-v1.5.

    Args:
        chunks: List of Chunk objects to embed
        prefix: Nomic task prefix (search_document for indexing)

    Returns:
        Same chunks with embeddings populated
    """
    if not chunks:
        return chunks

    model = _get_model()
    texts = [prefix + c.text for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    for chunk, emb in zip(chunks, embeddings):
        chunk.embedding = emb

    return chunks


def rank_chunks(
    chunks: list[Chunk],
    query: str,
    *,
    top_k: int = 5,
    prefix: str = "search_query: ",
) -> list[Chunk]:
    """
    Rank chunks by cosine similarity to query.

    Args:
        chunks: Embedded chunks
        query: Search query string
        top_k: Number of results to return
        prefix: Nomic task prefix (search_query for queries)

    Returns:
        Top-K chunks sorted by descending similarity score
    """
    if not chunks or not query:
        return chunks[:top_k]

    model = _get_model()
    q_emb = model.encode(
        [prefix + query],
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0]

    # Score all chunks
    for chunk in chunks:
        if chunk.embedding is not None:
            chunk.score = float(np.dot(q_emb, chunk.embedding))
        else:
            chunk.score = 0.0

    # Sort by score descending
    ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
    return ranked[:top_k]
