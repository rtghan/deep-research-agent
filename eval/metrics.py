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
5. Claim evolution: revision rate, reversals, retractions, and support lift
   → measures whether claims actually improve as evidence accumulates,
     rather than merely accumulating alongside it

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

    # --- Claim evolution (challenger → reviser loop) ---
    challenges_issued: int = 0
    challenge_hit_rate: float = 0.0   # % of challenges that found something wrong
    revision_rate: float = 0.0        # % of claims revised at least once
    refine_count: int = 0
    narrow_count: int = 0
    reversal_count: int = 0
    retraction_count: int = 0
    avg_reasoning_score: float = 0.0
    avg_evidence_balance: float = 0.0
    # The headline: did revising claims actually improve their grounding?
    support_lift: float = 0.0         # mean (support_after - support_before)
    revisions_improving: int = 0      # revisions where support went up
    revisions_worsening: int = 0      # revisions where support went down

    # --- Second, independent quality signal (see src/scoring/judge.py) ---
    # support_lift conflates "more accurate" with "harder to fully entail" — a
    # correct, hedged claim often scores lower on strict entailment than a
    # bold, simple one did. judge_improved_rate answers a structurally
    # different question (blind pairwise: which text better reflects the
    # evidence?) and doesn't share that conflation. Read the two together.
    judge_improved_count: int = 0
    judge_worse_count: int = 0
    judge_same_count: int = 0
    judge_improved_rate: float = 0.0  # improved / (improved + worse + same)

    # --- Reversal-gate diagnostics (min_sources_for_reversal, quote-grounding) ---
    # See DECISIONS.md D022 / TESTING.md for what these catch: a single thin
    # source outvoting everything else, and refutation claimed from evidence
    # that never actually discusses the claim.
    dropped_ungrounded_refutations: int = 0

    def to_dict(self) -> dict:
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in self.__dict__.items()}


def _compute_evolution_metrics(state: ResearchState, metrics: Metrics) -> None:
    """
    Evolution metrics.

    challenge_hit_rate is the self-agreement bias probe: hold evidence fixed and
    swap the challenger from a foreign model to the extractor's own, and the
    fraction of challenges that find anything wrong should drop. If it doesn't,
    the "use a separate model" defense isn't buying what we claim it does.

    support_lift is the load-bearing quality number. Claim evolution is only
    worth its cost if revised claims end up better grounded than they started —
    measured by re-verifying each revision against the evidence and differencing
    the support scores. A negative lift would mean revision is making claims
    WORSE, which no other metric here would catch.
    """
    all_claims = state.claims
    metrics.challenges_issued = len(state.challenges)
    if state.challenges:
        landed = sum(1 for ch in state.challenges if ch.led_to_revision)
        metrics.challenge_hit_rate = landed / len(state.challenges)
        metrics.avg_reasoning_score = float(
            np.mean([ch.reasoning_score for ch in state.challenges])
        )
        metrics.avg_evidence_balance = float(
            np.mean([ch.evidence_balance for ch in state.challenges])
        )
        metrics.dropped_ungrounded_refutations = sum(
            ch.dropped_ungrounded_refutations for ch in state.challenges
        )

    revised_claims = [c for c in all_claims if c.revisions]
    if all_claims:
        metrics.revision_rate = len(revised_claims) / len(all_claims)

    deltas = []
    for claim in all_claims:
        for rev in claim.revisions:
            if rev.operation == "refine":
                metrics.refine_count += 1
            elif rev.operation == "narrow":
                metrics.narrow_count += 1
            elif rev.operation == "reverse":
                metrics.reversal_count += 1
            elif rev.operation == "retract":
                metrics.retraction_count += 1

            if rev.support_before is not None and rev.support_after is not None:
                delta = rev.support_after - rev.support_before
                deltas.append(delta)
                if delta > 0:
                    metrics.revisions_improving += 1
                elif delta < 0:
                    metrics.revisions_worsening += 1

            if rev.judge_verdict == "improved":
                metrics.judge_improved_count += 1
            elif rev.judge_verdict == "worse":
                metrics.judge_worse_count += 1
            elif rev.judge_verdict == "same":
                metrics.judge_same_count += 1

    if deltas:
        metrics.support_lift = float(np.mean(deltas))

    judged_total = metrics.judge_improved_count + metrics.judge_worse_count + metrics.judge_same_count
    if judged_total:
        metrics.judge_improved_rate = metrics.judge_improved_count / judged_total


def compute_metrics(
    state: ResearchState,
    support_threshold: float = 0.4,
    num_bins: int = 10,
) -> Metrics:
    """Compute all metrics for a completed research run."""
    metrics = Metrics()

    # Evolution stats cover ALL claims, including retracted ones — a retraction
    # is an outcome of the process, not an absence of one.
    _compute_evolution_metrics(state, metrics)

    # Quality metrics cover SURVIVING claims only. Retracted claims must not sit
    # in the support-rate denominator: the system withdrew them precisely
    # because they were unsupported, so counting them would make retraction look
    # like a quality regression instead of the system working.
    claims = [c for c in state.claims if c.is_active]

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
