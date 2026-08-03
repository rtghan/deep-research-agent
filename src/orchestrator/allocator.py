"""
Adaptive Compute Allocator — budgets retrieval rounds per sub-question.

Design decision: the allocator maps difficulty → compute budget.
- Easy sub-questions get min_budget rounds (1)
- Hard sub-questions get max_budget rounds (4)
- Linear interpolation in between

The feedback loop:
1. Initial difficulty estimate (linguistic) → initial budget
2. After round 1, extractor + verifier produce confidence scores
3. Difficulty is updated using confidence
4. If confidence is still low, allocate additional rounds (up to max_budget)
5. Each additional round brings new evidence → re-extract → re-verify

This loop is the "adaptive test-time compute" — the system spends more
inference on harder sub-questions, guided by the verifier's confidence.

In uniform mode (ablation baseline), every sub-question gets the same budget.
The cost-quality curve compares adaptive vs. uniform.
"""

from __future__ import annotations

from src.obs.trace import log_step
from src.orchestrator.config import Config
from src.orchestrator.state import ResearchState, SubQuestion
from src.scoring.difficulty import (
    estimate_difficulty_linguistic,
    update_difficulty_from_confidence,
)


def allocate_initial_budgets(state: ResearchState, config: Config) -> None:
    """
    Phase 1: allocate initial budgets based on linguistic difficulty.
    Called after planning, before any retrieval.
    """
    if not state.plan:
        return

    adaptive = config.adaptive
    for sq in state.plan.sub_questions:
        if adaptive.enabled:
            sq.difficulty = estimate_difficulty_linguistic(sq)
            # Linear interpolation: difficulty 0 → min_budget, difficulty 1 → max_budget
            sq.compute_budget = int(
                adaptive.min_budget
                + sq.difficulty * (adaptive.max_budget - adaptive.min_budget)
            )
        else:
            # Uniform allocation (ablation baseline)
            sq.compute_budget = adaptive.max_budget

    log_step(
        state,
        component="allocator",
        step="initial_allocation",
        input_summary=f"{len(state.plan.sub_questions)} sub-questions, adaptive={adaptive.enabled}",
        output_summary=f"budgets: {[sq.compute_budget for sq in state.plan.sub_questions]}",
        latency_ms=0,
        cost_tokens=0,
        metadata={
            "mode": "adaptive" if adaptive.enabled else "uniform",
            "difficulties": [sq.difficulty for sq in state.plan.sub_questions],
            "budgets": [sq.compute_budget for sq in state.plan.sub_questions],
        },
    )


def _claims_churned_last_round(state: ResearchState, sq: SubQuestion) -> bool:
    """
    Did any claim for this sub-question get revised in the round just finished?

    This is the evolution loop's own convergence signal, distinct from
    confidence: a claim can be revised into a high-confidence form and still be
    unstable, because the next batch of evidence may narrow or reverse it again.
    """
    for claim in state.claims:
        if claim.sub_question_id != sq.sq_id:
            continue
        for rev in claim.revisions:
            if rev.round_num == sq.rounds_used:
                return True
    return False


def should_continue(
    state: ResearchState,
    sq: SubQuestion,
    claims: list | None = None,
    config: Config | None = None,
) -> bool:
    """
    Decide whether to run another retrieval round for this sub-question.

    Continues if:
    1. Rounds used < compute_budget, AND
    2. (Adaptive mode) average claim confidence is below threshold
       (i.e., the system is still uncertain → try harder)
    """
    if sq.rounds_used >= sq.compute_budget:
        return False

    if not config.adaptive.enabled:
        return sq.rounds_used < sq.compute_budget

    # Adaptive: check if confidence is still low
    sq_claims = [c for c in state.claims if c.sub_question_id == sq.sq_id and c.is_active]
    if not sq_claims:
        return True  # no claims yet, definitely continue

    # Claim churn: if the last round's evidence forced revisions, the picture is
    # still moving and another round is likely to keep moving it. A sub-question
    # whose claims are still being rewritten has not converged, however confident
    # the individual claims currently look.
    if config.evolution.enabled and _claims_churned_last_round(state, sq):
        log_step(
            state,
            component="allocator",
            step="continue_on_churn",
            input_summary=f"SQ: {sq.question[:60]}",
            output_summary="claims still being revised — not converged",
            latency_ms=0,
            cost_tokens=0,
        )
        return True

    avg_confidence = sum(c.confidence or 0.5 for c in sq_claims) / len(sq_claims)
    if avg_confidence < config.adaptive.low_confidence_threshold:
        # Low confidence → update difficulty and potentially extend budget
        new_difficulty = update_difficulty_from_confidence(state, sq, config)
        sq.difficulty = new_difficulty
        # Extend budget up to max_budget if difficulty increased
        new_budget = int(
            config.adaptive.min_budget
            + new_difficulty * (config.adaptive.max_budget - config.adaptive.min_budget)
        )
        if new_budget > sq.compute_budget:
            sq.compute_budget = min(new_budget, config.adaptive.max_budget)
            log_step(
                state,
                component="allocator",
                step="extend_budget",
                input_summary=f"SQ: {sq.question[:60]}, confidence={avg_confidence:.2f}",
                output_summary=f"budget extended: {sq.compute_budget - 1} -> {sq.compute_budget}",
                latency_ms=0,
                cost_tokens=0,
            )
        return True

    return False


# --- Wrapper function matching pipeline.py's expected interface ---

def allocate_budget(state: ResearchState, sq: SubQuestion, config: Config) -> None:
    """Per-sub-question budget allocation wrapper."""
    adaptive = config.adaptive
    if adaptive.enabled:
        # difficulty was already estimated by estimate_difficulty()
        sq.compute_budget = int(
            adaptive.min_budget
            + sq.difficulty * (adaptive.max_budget - adaptive.min_budget)
        )
    else:
        sq.compute_budget = adaptive.max_budget

    log_step(
        state,
        component="allocator",
        step="allocate",
        input_summary=f"SQ: {sq.question[:60]}, difficulty={sq.difficulty:.2f}",
        output_summary=f"budget={sq.compute_budget}",
        latency_ms=0,
        cost_tokens=0,
        metadata={
            "mode": "adaptive" if adaptive.enabled else "uniform",
            "budget": sq.compute_budget,
            "difficulty": sq.difficulty,
        },
    )
