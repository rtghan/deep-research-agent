"""
Text chunker — splits retrieved text into evidence chunks with provenance.

Design decision: simple fixed-size chunking with overlap. More sophisticated
chunking (semantic, sentence-boundary-aware) is a potential improvement but
not a depth track. Fixed-size is predictable and sufficient for the claim
extractor, which works at the chunk level.

Why overlap? To avoid splitting a key sentence across chunk boundaries, which
would lose context for the verifier.
"""

from __future__ import annotations

from src.orchestrator.state import EvidenceChunk


def chunk_text(
    text: str,
    source_url: str,
    source_title: str,
    source_type: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
    retrieval_round: int = 0,
    chunk_id_prefix: str = "chunk",
    sub_question_id: str | None = None,
) -> list[EvidenceChunk]:
    """
    Split text into overlapping chunks, each tagged with provenance.
    """
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_text_str = text[start:end].strip()
        if chunk_text_str:
            chunks.append(EvidenceChunk(
                chunk_id=f"{chunk_id_prefix}_{idx}",
                source_url=source_url,
                source_title=source_title,
                source_type=source_type,
                text=chunk_text_str,
                offset_start=start,
                offset_end=end,
                retrieval_round=retrieval_round,
                sub_question_id=sub_question_id,
            ))
            idx += 1
        start += chunk_size - chunk_overlap
        if start >= len(text):
            break

    return chunks
