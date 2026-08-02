"""
Metrics — compute the evaluation metrics for a research run.

Design decision: four metrics, each mapped to a brief requirement:
1. Claim-support rate: % of claims with support_score >= threshold
   → measures "separate claims from evidence with confidence"
2. Calibration error (ECE): Expected Calibration Error
   → measures "plausible vs. correct output" (Track B wow moment)
3. Contradiction recall: % of known contradictions detected
   → measures multi-source synthesis quality
4. Cost & latency: total tokens and time
   → measures efficiency (Track A: adaptive vs. uniform)

ECE (Expected Calibration Error) is the standard metric for calibration:
- Bin claims by predicted confidence (e.g., 0-10%, 10-20%, ..., 90-100%)
- For each bin, compute |accuracy - avg_confidence|
- Weight by bin size
- Lower ECE = better calibrated (0 = perfect)
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass

from src.orchestrator.state import ResearchState


@dataclass
class Metrics:
    claim_support_rate: float = 0.0
    calibration_error: float = 0.0
    contradiction_count: int = 0
    total_claims: int = 0
    supported_claims: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    total_evidence: int = 0
    avg_confidence: float = 0.0
    avg_difficulty: float = 0.0
    total_rounds: int = 0

    def to_dict(self) -> dict:
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in self.__dict__.items()}


def compute_metrics(
    state: ResearchState,
    support_threshold: float = 0.4,
    num_bins: int = 10,
) -> Metrics:
    """Compute all metrics for a completed research run."""
    claims = state.claims
    metrics = Metrics()

    if not claims:
        return metrics

    metrics.total_claims = len(claims)

    # 1. Claim-support rate
    supported = [c for c in claims if (c.support_score or 0) >= support_threshold]
    metrics.supported_claims = len(supported)
    metrics.claim_support_rate = len(supported) / len(claims)

    # 2. Calibration error (ECE)
    # For calibration, we need "ground truth" — whether each claim is actually correct.
    # Since we don't have ground truth labels, we use support_score as a proxy:
    # claims with support_score >= 0.5 are "correct", below are "incorrect".
    # This is a proxy calibration (documented in DECISIONS.md).
    confidences = np.array([c.confidence or 0.5 for c in claims])
    accuracies = np.array([1.0 if (c.support_score or 0) >= 0.5 else 0.0 for c in claims])

    bin_edges = np.linspace(0, 1, num_bins + 1)
    ece = 0.0
    for i in range(num_bins):
        mask = (confidences >= bin_edges[i]) & (confidences < bin_edges[i + 1])
        if i == num_bins - 1:  # include 1.0 in last bin
            mask = (confidences >= bin_edges[i]) & (confidences <= bin_edges[i + 1])
        bin_size = mask.sum()
        if bin_size > 0:
            bin_acc = accuracies[mask].mean()
            bin_conf = confidences[mask].mean()
            ece += (bin_size / len(claims)) * abs(bin_acc - bin_conf)
    metrics.calibration_error = ece

    # 3. Contradiction count
    metrics.contradiction_count = len(state.contradictions)

    # 4. Cost & latency
    metrics.total_tokens = state.total_tokens
    metrics.total_latency_ms = state.total_latency_ms
    metrics.total_evidence = len(state.evidence)

    # Additional stats
    metrics.avg_confidence = float(np.mean(confidences))
    if state.plan:
        metrics.avg_difficulty = float(np.mean([sq.difficulty for sq in state.plan.sub_questions]))
        metrics.total_rounds = sum(sq.rounds_used for sq in state.plan.sub_questions)

    return metrics
