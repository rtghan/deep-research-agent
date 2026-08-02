"""
Reviser — rewrites a claim so it reflects the evidence actually gathered.

The challenger diagnoses; the reviser edits. The split matters: the model that
decides a claim is wrong should not also be the one that gets to decide how
convenient the fix is. Keeping diagnosis and repair separate means the critique
is on record in the trace even when the repair is poor.

The reviser is told WHICH operation to perform — it does not choose. The
operation comes from the evidence-balance router in
src/orchestrator/evolution.py, so the decision "add nuance" vs. "flip the
position" is a threshold on how many distinct sources disagree, not an LLM
judgement call. The reviser's job is purely to execute that operation faithfully
against the evidence.

Operations:
  refine   — same position, tighter wording (fixes unsound reasoning, not scope)
  narrow   — same position, scoped to the conditions the evidence supports;
             this is where minority contradicting evidence becomes nuance
  reverse  — the position flips, because refuting evidence is now dominant
  retract  — the evidence no longer licenses any version of this claim
"""

from __future__ import annotations

from dataclasses import dataclass

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import Claim, EvidenceChunk, ResearchState


REVISER_SYSTEM = """You revise research claims so they match the evidence that has actually been gathered.

You will be given a claim, an adversarial critique of it, the evidence pool, and ONE operation to perform. Perform exactly that operation.

- "refine": Keep the claim's position. Fix imprecision and unsound reasoning.
  Replace vague assertions with the specific quantities, benchmarks, or
  conditions the evidence states. Do not broaden the claim.

- "narrow": Keep the claim's position, but restrict it to the conditions the
  evidence actually supports, and state the qualification explicitly. The
  refuting evidence is real but is the minority — the revised claim must
  acknowledge it rather than ignore it.
  Example: "X outperforms Y" → "X outperforms Y on long-context benchmarks,
  though on short inputs the reported gap disappears".

- "reverse": The weight of evidence now points the other way. State the
  position the evidence actually supports. Do not hedge into meaninglessness —
  assert the new position as clearly as the old one was asserted, scoped to
  what the evidence shows.

- "retract": The evidence does not license this claim. Return the operation
  "retract" and a one-sentence reason. Leave revised_text empty.

Rules for the revised claim:
- It must remain a SINGLE atomic, independently verifiable statement.
- Every part of it must be traceable to the evidence provided.
- Cite evidence by index in evidence_indices — include the refuting evidence
  you incorporated, not only the supporting evidence.
- Do not invent facts to patch the claim. If the fix requires evidence you were
  not given, retract instead.

Respond as JSON: {
    "operation": "refine" | "narrow" | "reverse" | "retract",
    "revised_text": "the rewritten claim (empty if retracting)",
    "evidence_indices": [int, ...],
    "rationale": "one sentence: what changed and which evidence forced it"
}"""


@dataclass
class RevisionResult:
    """The reviser's output for one claim."""
    operation: str
    revised_text: str = ""
    evidence_ids: list[str] = None
    rationale: str = ""
    changed: bool = False

    def __post_init__(self):
        if self.evidence_ids is None:
            self.evidence_ids = []


def _format_evidence_pool(pool: list[EvidenceChunk], refuting_ids: set[str]) -> str:
    """Render the pool, flagging the chunks the challenger read as refuting."""
    lines = []
    for i, chunk in enumerate(pool):
        marker = " [CHALLENGER FLAGGED AS REFUTING]" if chunk.chunk_id in refuting_ids else ""
        lines.append(
            f"\n--- Evidence [{i}] (source: {chunk.source_title}, "
            f"round: {chunk.retrieval_round}){marker} ---\n{chunk.text[:700]}"
        )
    return "\n".join(lines)


def revise_claim(
    state: ResearchState,
    claim: Claim,
    challenge,
    operation: str,
    evidence_pool: list[EvidenceChunk],
    llm,
    config: Config,
    round_num: int = 0,
) -> RevisionResult:
    """
    Execute one revision operation on a claim. Returns the proposed revision;
    the caller (evolution.py) applies it and records the version bump.
    """
    if operation == "retract":
        # No LLM call needed — the router already decided, on evidence balance,
        # that nothing here is salvageable. Paying a model to agree adds cost
        # and a chance of it talking itself out of the retraction.
        return RevisionResult(
            operation="retract",
            revised_text="",
            rationale=challenge.critique or "Evidence does not license this claim.",
            changed=True,
        )

    refuting_ids = set(challenge.refuting_evidence_ids)
    evidence_text = _format_evidence_pool(evidence_pool, refuting_ids)

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=REVISER_SYSTEM,
            user=(
                f"Claim (version {claim.version}): {claim.text}\n\n"
                f"Adversarial critique: {challenge.critique}\n"
                f"Flaws identified: {', '.join(challenge.flaws) if challenge.flaws else 'none named'}\n"
                f"Reasoning soundness: {challenge.reasoning_score:.2f}\n"
                f"Evidence balance: {challenge.evidence_balance:+.2f} "
                f"({challenge.n_supporting_sources} sources support, "
                f"{challenge.n_refuting_sources} refute)\n\n"
                f"Evidence pool:\n{evidence_text}\n\n"
                f"OPERATION TO PERFORM: {operation}"
            ),
        )

    revised_text = str(result.get("revised_text", "") or "").strip()
    returned_op = result.get("operation", operation)
    if returned_op not in ("refine", "narrow", "reverse", "retract"):
        returned_op = operation

    # The reviser is allowed to escalate to retract (it may find the claim
    # unsalvageable while rewriting) but not to quietly downgrade the operation
    # the router chose — that would let it dodge an inconvenient reversal.
    if returned_op != operation and returned_op != "retract":
        returned_op = operation

    if returned_op == "retract" or not revised_text:
        return RevisionResult(
            operation="retract" if returned_op == "retract" else operation,
            revised_text="",
            rationale=str(result.get("rationale", ""))[:300],
            changed=returned_op == "retract",
        )

    evidence_ids = []
    for idx in result.get("evidence_indices", []) or []:
        if isinstance(idx, int) and 0 <= idx < len(evidence_pool):
            evidence_ids.append(evidence_pool[idx].chunk_id)
    if not evidence_ids:
        # Fall back to the challenger's reading of the evidence rather than
        # leaving the revised claim uncited (which would make it unverifiable).
        evidence_ids = list(
            dict.fromkeys(challenge.supporting_evidence_ids + challenge.refuting_evidence_ids)
        ) or list(claim.evidence_ids)

    changed = revised_text.strip() != claim.text.strip()

    log_step(
        state,
        component="reviser",
        step=returned_op,
        input_summary=f"v{claim.version}: {claim.text[:80]}",
        output_summary=f"v{claim.version + 1}: {revised_text[:80]}" if changed else "unchanged",
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
        metadata={
            "claim_id": claim.claim_id,
            "operation": returned_op,
            "round": round_num,
            "changed": changed,
        },
    )

    return RevisionResult(
        operation=returned_op,
        revised_text=revised_text,
        evidence_ids=evidence_ids,
        rationale=str(result.get("rationale", ""))[:300],
        changed=changed,
    )
