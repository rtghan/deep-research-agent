"""
Judge — an independent, blind quality check on a claim revision.

WHY THIS EXISTS. The 2026-08 real-model evaluation (see TESTING.md) measured
revision quality with `support_lift`: the change in the verifier's support
score before vs. after a revision. Across three of four test cases,
support_lift was NEGATIVE despite qualitative inspection showing many
revisions were genuine, correct improvements. The reason is a conflation
support_lift can't see past: a hedged, nuanced, or corrected claim is
frequently HARDER to score as fully entailed by a single cited chunk than a
bold, simple, possibly-overreaching one was — even when the hedge is exactly
right. "GPT-4V's improvement varies significantly by dataset" is a more
accurate statement than "GPT-4V improves accuracy by 7% across all datasets",
and also a harder statement to verify at support_score=1.0, because it now
makes a claim about VARIATION that requires evidence about multiple datasets
to fully entail, not just one.

The judge asks a structurally different question, so it doesn't share that
conflation: given the original and revised text, unlabeled and in random
order, which one is a better reflection of what the FULL evidence pool shows?
It never sees which one is "the revision", so it can't shortcut on the
assumption that revisions are improvements.

This is a genuinely independent second signal, not a replacement for
support_lift — the two together (entailment strength AND holistic quality)
triangulate revision quality better than either alone.
"""

from __future__ import annotations

import random

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import EvidenceChunk, ResearchState


JUDGE_SYSTEM = """You are comparing two versions of a research claim to determine which one better reflects the evidence.

You will see two claim texts, labeled A and B. The order is RANDOMIZED — do not assume either position is "the original" or "the revision".

Judge ONLY: which claim is a more accurate, better-warranted reflection of what the evidence pool as a whole actually shows? A more hedged or qualified claim is not automatically better — reward accuracy and appropriate scope, not vagueness for its own sake. A bolder claim is not automatically worse if the evidence actually supports it unconditionally.

Respond as JSON: {
    "better": "A" | "B" | "equivalent",
    "reasoning": "one or two sentences on what decided it"
}"""


def judge_revision(
    state: ResearchState,
    original_text: str,
    revised_text: str,
    evidence_pool: list[EvidenceChunk],
    llm,
    config: Config,
    round_num: int = 0,
    claim_id: str = "",
) -> tuple[str, str]:
    """
    Blind pairwise judgement of a revision.

    Returns (verdict, rationale) where verdict is one of
    "improved" | "worse" | "same" | "uncertain". "uncertain" means judging is
    disabled or the response couldn't be parsed into a clear side.
    """
    if not config.evolution.judge_revisions:
        return "uncertain", ""

    # Randomize A/B order so the judge cannot use position as a shortcut for
    # "the second one must be the fix".
    original_is_a = random.random() < 0.5
    a_text, b_text = (original_text, revised_text) if original_is_a else (revised_text, original_text)

    evidence_text = "\n".join(
        f"--- Evidence [{i}] (source: {c.source_title}) ---\n{c.text[:500]}"
        for i, c in enumerate(evidence_pool)
    ) or "(no evidence pool available)"

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=JUDGE_SYSTEM,
            user=f"Claim A: {a_text}\n\nClaim B: {b_text}\n\nEvidence pool:\n{evidence_text}",
        )

    better = result.get("better", "equivalent")
    if better not in ("A", "B", "equivalent"):
        better = "equivalent"
    reasoning = str(result.get("reasoning", ""))[:300]

    if better == "equivalent":
        verdict = "same"
    elif (better == "A") == original_is_a:
        # The judge picked whichever label happened to be the ORIGINAL text.
        verdict = "worse"
    else:
        verdict = "improved"

    log_step(
        state, component="judge", step="judge_revision",
        input_summary=f"claim {claim_id}: comparing v_before vs v_after",
        output_summary=f"verdict={verdict}",
        latency_ms=timer.ms, cost_tokens=resp.total_tokens,
        metadata={"claim_id": claim_id, "round": round_num, "verdict": verdict},
    )

    return verdict, reasoning
