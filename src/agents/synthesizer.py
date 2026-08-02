"""
Synthesizer — produces a structured research report from verified claims.

The synthesizer does NOT generate new content from scratch.
It organizes and presents the claims that survived verification, grouped by
sub-question, with confidence scores and source citations visible.

The report makes the confidence and evidence
visible to the reader. A claim with 60% confidence is shown as such, not
presented as certain.

The report includes:
1. Executive summary (high-confidence claims only)
2. Findings by sub-question (with confidence and sources)
3. Contradictions found (with both sides)
4. Known gaps (sub-questions with insufficient evidence)
"""

from __future__ import annotations

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import Claim, ResearchState
from src.tools.base import LLMClient


SYNTHESIS_SYSTEM = """You are a research synthesizer. Given verified claims with confidence scores and evidence, write a structured research report.

Rules:
- Only present claims as facts if their confidence is above 0.6.
- For claims below 0.6 confidence, frame them as uncertain: "Evidence suggests..." or "There is disagreement about..."
- Always cite sources inline: [Source: paper title]
- If claims contradict each other, present both sides explicitly.
- If a sub-question has insufficient evidence, say so in a "Known Gaps" section.
- Be precise and quantitative where possible.

Output a markdown report with these sections:
## Executive Summary
## Findings
### [Sub-question 1]
### [Sub-question 2]
...
## Contradictions & Disagreements
## Known Gaps & Limitations"""


def synthesize(
    state: ResearchState,
    llm: LLMClient,
    config: Config,
) -> None:
    """Produce the final research report from verified claims."""
    if not state.plan or not state.claims:
        state.report = "# Research Report\n\nInsufficient evidence to generate a report."
        return

    evidence_map = {c.chunk_id: c for c in state.evidence}

    # Build the claims context for the LLM
    claims_by_sq = {}
    for claim in state.claims:
        sq_id = claim.sub_question_id or "unassigned"
        if sq_id not in claims_by_sq:
            claims_by_sq[sq_id] = []
        claims_by_sq[sq_id].append(claim)

    context = f"# Original Query\n{state.query}\n\n"
    context += f"# Clarified Query\n{state.plan.clarified_query or state.query}\n\n"
    context += "# Verified Claims (by sub-question)\n\n"

    for sq in state.plan.sub_questions:
        sq_claims = claims_by_sq.get(sq.sq_id, [])
        context += f"## Sub-question: {sq.question}\n"
        context += f"(Difficulty: {sq.difficulty:.2f}, Compute rounds: {sq.rounds_used})\n\n"
        for claim in sq_claims:
            conf = claim.confidence or 0.0
            status = claim.verification_status or "unverified"
            sources = []
            for eid in claim.evidence_ids:
                chunk = evidence_map.get(eid)
                if chunk:
                    sources.append(chunk.source_title)
            src_str = "; ".join(sources) if sources else "no source"
            context += f"- [confidence={conf:.2f}, status={status}] {claim.text} [Source: {src_str}]\n"
        context += "\n"

    if state.contradictions:
        context += "# Detected Contradictions\n\n"
        for con in state.contradictions:
            claim_a = next((c for c in state.claims if c.claim_id == con.claim_a_id), None)
            claim_b = next((c for c in state.claims if c.claim_id == con.claim_b_id), None)
            context += f"- {con.description}\n"
            context += f"  Side A ({con.source_a}): {claim_a.text if claim_a else 'N/A'}\n"
            context += f"  Side B ({con.source_b}): {claim_b.text if claim_b else 'N/A'}\n\n"

    with Timer() as timer:
        resp = llm.complete(
            system=SYNTHESIS_SYSTEM,
            user=f"Synthesize a research report from the following verified claims:\n\n{context}",
        )

    state.report = resp.text

    log_step(
        state,
        component="synthesizer",
        step="synthesize",
        input_summary=f"{len(state.claims)} claims, {len(state.contradictions)} contradictions",
        output_summary=f"Report: {len(resp.text)} chars",
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
    )
