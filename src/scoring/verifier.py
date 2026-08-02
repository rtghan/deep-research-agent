"""
Verifier — checks each claim's support against its evidence.

For each claim, the verifier:
1. Reads the claim text
2. Reads the linked evidence chunks
3. Assesses: is this claim supported, contradicted, or insufficient?
4. Assigns a support score (0.0 = contradicted, 1.0 = fully supported)

Should use a DIFFERENT model for verification than extraction
(verifier_model vs. sub_step_model). This avoids the self-agreement bias
where the same model that generated a claim tends to agree with itself.

The support score feeds two things:
- the confidence scorer uses it for calibration
- the allocator uses it as a difficulty signal (low support = hard)

Cross-source contradiction detection: the verifier also checks claims
against evidence from DIFFERENT sources, flagging when Source A's evidence
contradicts Source B's claim.
"""

from __future__ import annotations

import json

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import Claim, Contradiction, EvidenceChunk, ResearchState
from src.tools.base import LLMClient


VERIFIER_SYSTEM = """You are an evidence verifier. Your job is to check whether a claim is supported by its evidence.

For each claim, assess:
1. Is the claim directly stated or clearly implied by the evidence?
2. Is there any evidence that CONTRADICTS the claim?
3. Is there insufficient evidence to judge?

Respond as JSON: {
    "support_score": float (0.0 = contradicted, 0.5 = insufficient, 1.0 = fully supported),
    "status": "supported" | "contradicted" | "insufficient",
    "reasoning": "one sentence explanation"
}"""


CONTRADICTION_SYSTEM = """You are a contradiction detector. Given claims from different sources, identify any contradictions.

Two claims contradict if they make opposing assertions about the same fact.
Respond as JSON: {"contradictions": [{"claim_a_idx": 0, "claim_b_idx": 1, "description": "what contradicts"}]}
If no contradictions, return an empty list."""


def _build_evidence_context(claim: Claim, evidence_map: dict[str, EvidenceChunk]) -> str:
    """Build the evidence text for a claim."""
    context = ""
    for i, eid in enumerate(claim.evidence_ids):
        chunk = evidence_map.get(eid)
        if chunk:
            context += f"\n--- Evidence [{i}] (source: {chunk.source_title}) ---\n"
            context += chunk.text[:600] + "\n"
    return context if context else "\n(No linked evidence available.)\n"


def verify_claims(
    state: ResearchState,
    claims: list[Claim],
    llm: LLMClient,
    config: Config,
) -> None:
    """Verify each claim against its evidence. Updates claim support scores in place."""
    if not config.verification.enabled or not claims:
        return

    evidence_map = {c.chunk_id: c for c in state.evidence}

    for claim in claims:
        evidence_context = _build_evidence_context(claim, evidence_map)

        with Timer() as timer:
            result, resp = llm.complete_json(
                system=VERIFIER_SYSTEM,
                user=f"Claim: {claim.text}\n\nEvidence:\n{evidence_context}\n\nIs this claim supported by the evidence?",
            )

        try:
            claim.support_score = float(result.get("support_score", 0.5))
        except (TypeError, ValueError):
            claim.support_score = 0.5
        status = result.get("status", "insufficient")
        claim.verification_status = status

        log_step(
            state,
            component="verifier",
            step="verify_claim",
            input_summary=f"Claim: {claim.text[:80]}",
            output_summary=f"status={status}, support={claim.support_score:.2f}",
            latency_ms=timer.ms,
            cost_tokens=resp.total_tokens,
            metadata={"claim_id": claim.claim_id, "support_score": claim.support_score},
        )


def detect_contradictions(
    state: ResearchState,
    claims: list[Claim],
    llm: LLMClient,
    config: Config,
) -> None:
    """Check for contradictions between claims from different sources."""
    if not config.verification.enabled or len(claims) < 2:
        return

    evidence_map = {c.chunk_id: c for c in state.evidence}

    # Group claims by source to find cross-source pairs
    claim_source_map = {}
    for claim in claims:
        sources = set()
        for eid in claim.evidence_ids:
            chunk = evidence_map.get(eid)
            if chunk:
                sources.add(chunk.source_title)
        claim_source_map[claim.claim_id] = sources

    # Build claim text list for the LLM
    claims_text = ""
    for i, claim in enumerate(claims):
        sources = claim_source_map.get(claim.claim_id, set())
        src_str = ", ".join(sources) if sources else "unknown"
        claims_text += f"[{i}] (source: {src_str}) {claim.text}\n"

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=CONTRADICTION_SYSTEM,
            user=f"Claims from different sources:\n{claims_text}\n\nIdentify any contradictions.",
        )

    for con in result.get("contradictions", []):
        a_idx = con.get("claim_a_idx", -1)
        b_idx = con.get("claim_b_idx", -1)
        if 0 <= a_idx < len(claims) and 0 <= b_idx < len(claims):
            claim_a = claims[a_idx]
            claim_b = claims[b_idx]
            # Only flag cross-source contradictions
            src_a = claim_source_map.get(claim_a.claim_id, set())
            src_b = claim_source_map.get(claim_b.claim_id, set())
            if not src_a.intersection(src_b):  # different sources
                state.contradictions.append(Contradiction(
                    claim_a_id=claim_a.claim_id,
                    claim_b_id=claim_b.claim_id,
                    description=con.get("description", ""),
                    source_a=", ".join(src_a),
                    source_b=", ".join(src_b),
                ))

    log_step(
        state,
        component="verifier",
        step="detect_contradictions",
        input_summary=f"{len(claims)} claims",
        output_summary=f"{len(state.contradictions)} contradictions found",
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
    )
