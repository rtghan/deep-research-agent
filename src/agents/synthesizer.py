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
- EVERY claim you state must carry an explicit confidence marker, in this exact
  format, at the end of the sentence:
      [confidence: 0.72 · supported · Source: paper title]
  Use the confidence value and verification status given for that claim in the
  input. Never invent a confidence value, and never omit the marker — a reader
  must be able to tell a 0.95 claim from a 0.55 one at a glance, without
  inferring it from your word choice.
- In ADDITION to the marker, match your language to the confidence:
  above 0.6 may be stated directly; at or below 0.6 must also be framed as
  uncertain ("Evidence suggests...", "One source reports...").
- The Executive Summary is the exception: write it as prose without inline
  markers, but do not assert anything there that is below 0.6 confidence
  without hedging it.
- If claims contradict each other, present both sides explicitly.
- If a sub-question has insufficient evidence, say so in a "Known Gaps" section.
- Be precise and quantitative where possible.
- Some claims were revised as evidence accumulated — they are marked with their
  version and the operation that produced it (narrowed, reversed, refined).
  Report the CURRENT text. Where a claim was reversed or retracted, say so in
  the "How Claims Changed" section: a research process that corrected itself is
  a finding, not an embarrassment to hide.

Every factual sentence in Findings must end with its confidence marker. A report where the reader cannot tell how sure the system is about each individual claim has failed its main purpose.

Output a markdown report with these sections:
## Executive Summary
## Findings
### [Sub-question 1]
### [Sub-question 2]
...
## Contradictions & Disagreements
## How Claims Changed
## Known Gaps & Limitations

Omit "How Claims Changed" only if no claim was revised."""


def _render_confidence_index(state, active_claims, evidence_map) -> str:
    """
    Deterministically append every claim with its calibrated confidence.

    WHY THIS IS CODE AND NOT A PROMPT INSTRUCTION. The brief's one hard
    formatting requirement is that the output "indicate how confident it is in
    each claim". That was first attempted by instructing the synthesizer to emit
    an inline `[confidence: 0.72 · supported · Source: …]` marker on every
    claim — explicitly, in a fixed format, stated twice in the system prompt.
    A real run (gpt-4o-mini) emitted ZERO of them. The model kept the `[Source:]`
    attribution it had been doing all along and silently dropped the new part.

    This is the same lesson quote-grounding taught in D022: when a property must
    hold, asking a model nicely does not make it hold. So the index is rendered
    from `state.claims` in code — it cannot be dropped, cannot be miscopied, and
    cannot drift from the confidence the system actually computed. The prompt
    instruction is retained as a best-effort improvement to the prose, but the
    requirement is satisfied here regardless of whether the model complies.

    It also surfaces information the prose has no good place for: which claims
    were revised, and which were retracted outright.
    """
    if not active_claims:
        return ""

    lines = [
        "\n\n---\n",
        "## Claim Confidence Index\n",
        "_Generated directly from the system's internal state, not written by the "
        "report model — every claim the report draws on, with the confidence the "
        "system actually assigned it._\n",
    ]

    by_sq = {}
    for c in active_claims:
        by_sq.setdefault(c.sub_question_id or "unassigned", []).append(c)

    sqs = state.plan.sub_questions if state.plan else []
    for sq in sqs:
        claims = by_sq.get(sq.sq_id, [])
        if not claims:
            continue
        lines.append(f"\n**{sq.question}**\n")
        lines.append("| Confidence | Status | Claim | Sources |")
        lines.append("|---|---|---|---|")
        for c in sorted(claims, key=lambda x: -(x.confidence or 0.0)):
            sources = []
            for eid in c.evidence_ids:
                chunk = evidence_map.get(eid)
                if chunk and chunk.source_title not in sources:
                    sources.append(chunk.source_title)
            src = "; ".join(s[:60] for s in sources[:3]) or "no source"
            status = c.verification_status or "unverified"
            if c.revisions:
                status += f" (v{c.version}, {c.revisions[-1].operation})"
            text = (c.text or "").replace("|", "\\|")
            lines.append(f"| {c.confidence or 0.0:.2f} | {status} | {text} | {src} |")

    # Oscillating claims are a FINDING, not noise. A claim that cycles between
    # wordings under repeated challenge means the evidence does not determine
    # the answer — reporting the last version the loop happened to land on
    # would be presenting an arbitrary coin-flip as a conclusion.
    oscillating = [c for c in active_claims if c.oscillating]
    if oscillating:
        lines.append(
            f"\n**Unresolved under repeated scrutiny ({len(oscillating)})** — these "
            f"claims changed position and then changed back when re-challenged "
            f"against the same evidence. The sources genuinely conflict; treat the "
            f"wording below as one defensible reading, not a settled conclusion.\n"
        )
        for c in oscillating:
            lines.append(f"- {(c.text or '')[:200]}")

    retracted = [c for c in state.claims if c.status == "retracted"]
    if retracted:
        lines.append(
            f"\n**Retracted during verification ({len(retracted)})** — extracted "
            f"from evidence, then withdrawn when challenged. Not used in the report above.\n"
        )
        for c in retracted:
            reason = c.revisions[-1].rationale if c.revisions else ""
            lines.append(f"- ~~{(c.text or '')[:180]}~~ — {reason[:160]}")

    return "\n".join(lines) + "\n"


def synthesize(
    state: ResearchState,
    llm: LLMClient,
    config: Config,
    critique=None,
) -> None:
    """
    Produce the final research report from verified claims.

    When `critique` is supplied (a ReportCritique from the report-level
    self-correction loop, Phase 5), this is a REWRITE: the previous report's
    diagnosed defects are appended to the prompt so the synthesizer fixes them
    rather than reproducing them.
    """
    # Retracted claims are excluded from the report body — the system withdrew
    # them — but their retraction is reported in "How Claims Changed" below.
    active_claims = [c for c in state.claims if c.is_active]
    retracted_claims = [c for c in state.claims if c.status == "retracted"]

    if not state.plan or not active_claims:
        note = ""
        if retracted_claims:
            note = (
                f"\n\nAll {len(retracted_claims)} extracted claim(s) were retracted "
                f"during verification — the evidence gathered did not support them."
            )
        state.report = f"# Research Report\n\nInsufficient evidence to generate a report.{note}"
        return

    evidence_map = {c.chunk_id: c for c in state.evidence}

    # Build the claims context for the LLM
    claims_by_sq = {}
    for claim in active_claims:
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
            version_note = ""
            if claim.revisions:
                last_op = claim.revisions[-1].operation
                version_note = f", v{claim.version}, last revised: {last_op}"
            context += (
                f"- [confidence={conf:.2f}, status={status}{version_note}] "
                f"{claim.text} [Source: {src_str}]\n"
            )
        context += "\n"

    if state.contradictions:
        context += "# Detected Contradictions\n\n"
        for con in state.contradictions:
            claim_a = next((c for c in state.claims if c.claim_id == con.claim_a_id), None)
            claim_b = next((c for c in state.claims if c.claim_id == con.claim_b_id), None)
            context += f"- {con.description}\n"
            context += f"  Side A ({con.source_a}): {claim_a.text if claim_a else 'N/A'}\n"
            context += f"  Side B ({con.source_b}): {claim_b.text if claim_b else 'N/A'}\n\n"

    # Claim evolution history — how the research process corrected itself.
    evolved = [c for c in state.claims if c.revisions]
    if evolved:
        context += "# How Claims Changed (claim evolution)\n\n"
        for claim in evolved:
            for rev in claim.revisions:
                if rev.operation == "retract":
                    context += (
                        f"- RETRACTED (round {rev.round_num}): \"{rev.prev_text}\"\n"
                        f"  Reason: {rev.rationale}\n"
                        f"  Evidence balance at retraction: {rev.evidence_balance:+.2f}\n\n"
                    )
                else:
                    delta = ""
                    if rev.support_before is not None and rev.support_after is not None:
                        delta = f" (support {rev.support_before:.2f} → {rev.support_after:.2f})"
                    context += (
                        f"- {rev.operation.upper()} in round {rev.round_num}{delta}:\n"
                        f"  Before: \"{rev.prev_text}\"\n"
                        f"  After:  \"{rev.new_text}\"\n"
                        f"  Why: {rev.rationale}\n"
                        f"  Evidence balance: {rev.evidence_balance:+.2f}"
                        f"{', flaws: ' + ', '.join(rev.flaws) if rev.flaws else ''}\n\n"
                    )

    # Rewrite mode: a previous version of this report was reviewed and found
    # wanting. Give the synthesizer the specific defects so it fixes them
    # instead of regenerating the same prose from the same claims.
    instruction = "Synthesize a research report from the following verified claims:"
    if critique is not None:
        instruction = (
            "You previously wrote a report from these claims and it was reviewed. "
            "Rewrite it, fixing the specific defects listed below. Keep what was "
            "working; do not introduce claims that are not in the list."
        )
        context += "\n# Review of your previous draft — fix these\n\n"
        if not critique.answers_the_question:
            context += (
                "- OVERALL: the previous draft did not actually answer the original "
                "question. Lead with a direct answer.\n"
            )
        for d in critique.defects:
            sq_str = f" [{d.sub_question_id}]" if d.sub_question_id else ""
            context += f"- ({d.severity}) {d.defect_type}{sq_str}: {d.detail}\n"
        if critique.revision_instructions:
            context += f"\nReviewer's directions: {critique.revision_instructions}\n"

    with Timer() as timer:
        resp = llm.complete(
            system=SYNTHESIS_SYSTEM,
            user=f"{instruction}\n\n{context}",
        )

    state.report = resp.text + _render_confidence_index(state, active_claims, evidence_map)

    log_step(
        state,
        component="synthesizer",
        step="synthesize",
        input_summary=(
            f"{len(active_claims)} active claims ({len(evolved)} revised, "
            f"{len(retracted_claims)} retracted), {len(state.contradictions)} contradictions"
        ),
        output_summary=f"Report: {len(resp.text)} chars",
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
    )
