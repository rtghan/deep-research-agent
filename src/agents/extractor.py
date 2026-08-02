"""
Claim Extractor — extracts atomic, verifiable claims from evidence chunks.

Design decision: the extractor reads evidence chunks for a sub-question and
produces atomic claims. "Atomic" means each claim states a single fact that
can be independently verified against the evidence. This is the foundation
of Track B — without atomic claims, verification is meaningless.

The extractor also links each claim to the evidence chunks it came from,
building the claim-evidence graph implicitly.
"""

from __future__ import annotations

import json

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import Claim, EvidenceChunk, ResearchState, SubQuestion
from src.tools.base import LLMClient


EXTRACTOR_SYSTEM = """You are a claim extractor. Given evidence chunks from research papers, extract atomic, independently verifiable claims.

Rules:
- Each claim must state a single fact (not a paragraph, not multiple facts).
- Each claim must be traceable to the evidence chunk it came from.
- Do NOT invent claims that aren't in the evidence. Only extract what's stated.
- Be precise: "X improves Y by Z%" not "X is good."

Respond as JSON: {"claims": [{"text": "the claim", "evidence_indices": [0, 1]}, ...]}

The evidence_indices refer to the indexed evidence chunks provided (0-based)."""


def extract_claims(
    state: ResearchState,
    sq: SubQuestion,
    evidence_chunks: list[EvidenceChunk],
    llm: LLMClient,
    config: Config,
) -> list[Claim]:
    """Extract atomic claims from evidence chunks for a sub-question."""
    if not evidence_chunks:
        return []

    # Build evidence context for the LLM
    evidence_text = ""
    for i, chunk in enumerate(evidence_chunks):
        evidence_text += f"\n--- Evidence [{i}] (source: {chunk.source_title}, type: {chunk.source_type}) ---\n"
        evidence_text += chunk.text[:800] + "\n"

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=EXTRACTOR_SYSTEM,
            user=f"Sub-question: {sq.question}\n\nEvidence chunks:\n{evidence_text}\n\nExtract atomic claims from this evidence.",
        )

    new_claims = []
    existing_count = len(state.claims)
    for i, c in enumerate(result.get("claims", [])):
        text = c.get("text", str(c)) if isinstance(c, dict) else str(c)
        evidence_indices = c.get("evidence_indices", []) if isinstance(c, dict) else []
        evidence_ids = []
        for idx in evidence_indices:
            if 0 <= idx < len(evidence_chunks):
                evidence_ids.append(evidence_chunks[idx].chunk_id)

        claim = Claim(
            claim_id=f"claim_{existing_count + i}",
            text=text,
            original_text=text,  # v1 text, preserved across later revisions
            evidence_ids=evidence_ids,
            sub_question_id=sq.sq_id,
        )
        new_claims.append(claim)
        state.claims.append(claim)
        sq.claim_ids.append(claim.claim_id)

    log_step(
        state,
        component="extractor",
        step="extract",
        input_summary=f"SQ: {sq.question[:80]}, {len(evidence_chunks)} chunks",
        output_summary=f"{len(new_claims)} claims extracted",
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
        metadata={"num_claims": len(new_claims)},
    )

    return new_claims
