"""
Confidence Scorer — calibrates per-claim confidence.

Design decision: confidence is NOT just the support score. It's a composite
that accounts for:
1. Support score (from verifier) — how well the evidence supports the claim
2. Evidence count — more independent sources = higher confidence
3. Source diversity — claims backed by multiple sources are more reliable
4. Contradiction penalty — claims involved in contradictions get penalized

The key innovation is CALIBRATION: we want "80% confident" to mean "right 80%
of the time." The reliability diagram (in eval/visualize.py) checks this.

Why calibration matters: an uncalibrated system that's always "90% confident"
is useless — you can't trust its confidence to make decisions. A calibrated
system lets you threshold: "only include claims above 70% confidence."
"""

from __future__ import annotations

import math

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import Claim, ResearchState


def score_confidence(
    state: ResearchState,
    claims: list[Claim],
    config: Config,
) -> None:
    """
    Assign calibrated confidence to each claim.

    Confidence = weighted combination of:
    - Support score (weight: 0.5) — primary signal
    - Evidence diversity (weight: 0.3) — more sources = more confidence
    - Contradiction penalty (weight: 0.2) — contradictions reduce confidence

    This is a heuristic calibrator. A proper calibrator would use temperature
    scaling on a held-out set, but that requires ground-truth labels which we
    don't have at inference time. The heuristic is transparent and explainable.
    """
    evidence_map = {c.chunk_id: c for c in state.evidence}
    contradiction_claim_ids = set()
    for con in state.contradictions:
        contradiction_claim_ids.add(con.claim_a_id)
        contradiction_claim_ids.add(con.claim_b_id)

    for claim in claims:
        if claim.support_score is None:
            claim.confidence = 0.3  # unverified = low confidence
            continue

        # Signal 1: Support score (0-1)
        support = claim.support_score

        # Signal 2: Evidence diversity — count distinct sources
        sources = set()
        for eid in claim.evidence_ids:
            chunk = evidence_map.get(eid)
            if chunk:
                sources.add(chunk.source_title)
        # Normalize: 1 source = 0.5, 2 = 0.75, 3+ = 1.0 (diminishing returns)
        diversity = min(1.0, 0.25 * len(sources) + 0.25) if sources else 0.1

        # Signal 3: Contradiction penalty
        contradiction_penalty = 0.3 if claim.claim_id in contradiction_claim_ids else 0.0

        # Weighted combination
        confidence = (0.5 * support + 0.3 * diversity) * (1.0 - contradiction_penalty)
        claim.confidence = max(0.0, min(1.0, confidence))

    log_step(
        state,
        component="confidence",
        step="score",
        input_summary=f"{len(claims)} claims",
        output_summary=f"confidence range: {min(c.confidence or 0 for c in claims):.2f}-{max(c.confidence or 0 for c in claims):.2f}",
        latency_ms=0,
        cost_tokens=0,
        metadata={
            "avg_confidence": sum(c.confidence or 0 for c in claims) / max(1, len(claims)),
            "contradicted_claims": len(contradiction_claim_ids),
        },
    )
