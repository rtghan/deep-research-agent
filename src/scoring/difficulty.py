"""
Difficulty Estimator — estimates how hard a sub-question is.

Before running
retrieval, we estimate each sub-question's difficulty to allocate compute.

Design decision: difficulty is estimated from TWO signals:
1. LINGUISTIC: query complexity (length, specificity, presence of
   comparison/contradiction keywords like "vs", "versus", "disagree")
2. SEMANTIC: after initial retrieval, the verifier's confidence on
   extracted claims becomes the primary difficulty signal

The two-phase approach:
- Phase 1 (pre-retrieval): linguistic estimate only (cheap, no API call)
- Phase 2 (post-retrieval): update with verifier confidence (grounded signal)
"""

from __future__ import annotations

import re

from src.obs.trace import log_step
from src.orchestrator.config import Config
from src.orchestrator.state import ResearchState, SubQuestion


# Keywords that indicate harder questions (comparative, contradictory, open-ended)
HARD_KEYWORDS = [
    "vs", "versus", "compared", "contrast", "contradict", "disagree",
    "debate", "controversy", "conflict", "differ", "dispute", "ongoing",
    "emerging", "novel", "recent", "unknown", "unclear", "limitation",
]
# Keywords that indicate easier questions (factual, definitional)
EASY_KEYWORDS = [
    "what is", "define", "definition", "describe", "overview",
    "introduction", "explain", "how does", "what are",
]


def estimate_difficulty_linguistic(sq: SubQuestion) -> float:
    """
    Phase 1: estimate difficulty from query features alone (pre-retrieval).
    Returns 0.0 (easy) to 1.0 (very hard).
    """
    q = sq.question.lower()
    word_count = len(sq.question.split())

    # Signal 1: keyword matching
    hard_hits = sum(1 for kw in HARD_KEYWORDS if kw in q)
    easy_hits = sum(1 for kw in EASY_KEYWORDS if kw in q)
    keyword_score = min(1.0, 0.2 * hard_hits - 0.15 * easy_hits + 0.3)
    keyword_score = max(0.1, keyword_score)

    # Signal 2: query length (longer queries tend to be more complex)
    length_score = min(1.0, word_count / 20.0)

    # Signal 3: question type (comparative questions are harder)
    type_score = 0.3  # default
    if any(kw in q for kw in ["vs", "versus", "compared", "contrast"]):
        type_score = 0.8  # comparative = hard
    elif any(kw in q for kw in ["what is", "define"]):
        type_score = 0.2  # definitional = easy

    # Weighted combination
    difficulty = 0.4 * keyword_score + 0.2 * length_score + 0.4 * type_score
    return max(0.0, min(1.0, difficulty))


def update_difficulty_from_confidence(
    state: ResearchState,
    sq: SubQuestion,
    config: Config,
) -> float:
    """
    Phase 2: update difficulty estimate using verifier confidence.

    If claims have low support scores, the sub-question is harder than
    the linguistic estimate suggested. This is the Track A ↔ Track B link:
    the verifier's output becomes the allocator's input.
    """
    # Get claims for this sub-question
    sq_claims = [c for c in state.claims if c.sub_question_id == sq.sq_id]
    if not sq_claims:
        return sq.difficulty

    # Average confidence of claims for this sub-question
    confidences = [c.confidence or 0.5 for c in sq_claims]
    avg_confidence = sum(confidences) / len(confidences)

    # Low confidence = high difficulty (inverse relationship)
    # Blend with the linguistic estimate: 60% confidence-based, 40% linguistic
    confidence_difficulty = 1.0 - avg_confidence
    updated = 0.6 * confidence_difficulty + 0.4 * sq.difficulty

    log_step(
        state,
        component="difficulty",
        step="update",
        input_summary=f"SQ: {sq.question[:60]}, avg_confidence={avg_confidence:.2f}",
        output_summary=f"difficulty: {sq.difficulty:.2f} -> {updated:.2f}",
        latency_ms=0,
        cost_tokens=0,
        metadata={
            "linguistic_difficulty": sq.difficulty,
            "confidence_difficulty": confidence_difficulty,
            "final_difficulty": updated,
        },
    )

    return max(0.0, min(1.0, updated))


# --- Wrapper functions matching pipeline.py's expected interface ---

def estimate_difficulty(state: ResearchState, sq: SubQuestion, config: Config) -> None:
    """Phase 1 wrapper: estimate difficulty linguistically and store on the sub-question."""
    sq.difficulty = estimate_difficulty_linguistic(sq)
    log_step(
        state,
        component="difficulty",
        step="estimate",
        input_summary=f"SQ: {sq.question[:60]}",
        output_summary=f"difficulty={sq.difficulty:.2f}",
        latency_ms=0,
        cost_tokens=0,
        metadata={"method": "linguistic", "difficulty": sq.difficulty},
    )


def update_difficulty(state: ResearchState, sq: SubQuestion, claims: list, config: Config) -> None:
    """Phase 2 wrapper: update difficulty using verifier confidence on new claims."""
    updated = update_difficulty_from_confidence(state, sq, config)
    sq.difficulty = updated
