"""
Report critic — checks whether the synthesized report actually answers the
question that was asked.

WHY THIS EXISTS. Everything upstream of this operates per-claim. The verifier
asks "does the cited evidence entail this claim". The challenger asks "is this
claim warranted by the evidence pool as a whole". Neither asks the question the
user actually cares about: *does the finished report answer what I asked?*

A report can be built entirely from well-verified, adversarially-survived claims
and still fail — by never addressing one of its own sub-questions, by stating a
0.4-confidence claim in flat declarative prose, by burying the answer under
background, by detecting contradictions and then not telling the reader about
them, or (a hard error) by quoting a claim the system already retracted.
ARCHITECTURE.md section 12.5 predicted exactly this gap: "the system does not
detect synthesis errors — cases where the individual claims are correct but the
report misinterprets or misassembles them."

TWO TIERS OF ERROR DETECTION. The critic is the second tier. The first tier is
`mechanical_checks()` in src/orchestrator/report_loop.py: deterministic,
LLM-free checks for defects that are decidable in code (a retracted claim's text
appearing in the report; a sub-question with zero surviving claims; detected
contradictions with no corresponding report section). Those findings are handed
to this critic as *starting evidence*, so the model spends its attention on the
judgment calls — overstatement, burial, whether the question was actually
answered — instead of re-deriving what a substring check already proved.

INDEPENDENT MODEL. The critic runs on a different model from the synthesizer,
for the same reason the challenger does (DECISIONS.md D021), and more sharply:
report-level defects are the ones the user actually sees, so a critic that
ratifies its own prose fails at the most consequential layer.
"""

from __future__ import annotations

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import (
    ReportCritique,
    ReportDefect,
    ResearchGap,
    ResearchState,
)


CRITIC_SYSTEM = """You are reviewing a research report produced by an automated research agent. Your job is to find where it fails the person who asked the question — not to praise it.

You are given: the original question, the sub-questions the agent chose to investigate, per-sub-question evidence statistics, the claims the report was built from (with confidence scores), any defects already found by automated checks, and the report itself.

Judge these things:

1. DOES IT ANSWER THE QUESTION? Not "is it about the right topic" — does a reader
   get a direct answer to what was actually asked? If the answer is buried under
   background, or the report describes the landscape without concluding
   anything, that is a failure even if every sentence is true.

2. OVERSTATEMENT. Cross-check the report's prose against the claim confidences
   given. A claim with confidence below 0.6 stated as flat fact is a defect.
   Quote the specific sentence.

3. UNSUPPORTED PROSE. Statements in the report that do not trace to any claim in
   the list. Synthesis is allowed to connect claims; it is not allowed to
   introduce new factual assertions.

4. MISSING COVERAGE. A sub-question the report does not meaningfully address, or
   addresses without acknowledging that the evidence was thin.

5. MISHANDLED CONTRADICTION. Detected disagreements that the report smooths over
   or omits instead of presenting both sides.

Then decide a verdict:
- "accept": the report answers the question and has no high-severity defects.
- "revise_report": the defects are presentational — overstatement, burial, poor
  organization, mishandled contradictions. The evidence base is adequate; the
  writing is not. This is fixed by rewriting, with no new research.
- "needs_more_research": the evidence base itself is inadequate. Some
  sub-question genuinely lacks the evidence needed to answer it, and no amount
  of rewriting will fix that. Only choose this if you can say specifically what
  is missing.

If and only if the verdict is "needs_more_research", fill in research_gaps. For
each, name the sub_question_id and state concretely WHAT TO GO FIND — this text
is fed to a search-query generator, so make it a description of missing
evidence ("head-to-head latency benchmarks under equal compute", "any source
disputing the scaling claim"), not a restatement of the sub-question.

Respond as JSON: {
    "answers_the_question": true | false,
    "verdict": "accept" | "revise_report" | "needs_more_research",
    "defects": [
        {"defect_type": "overstatement" | "unsupported_prose" | "missing_coverage" |
                        "buried_answer" | "mishandled_contradiction",
         "detail": "what is wrong, quoting the report where relevant",
         "sub_question_id": "sq_N or null",
         "severity": "high" | "medium" | "low"}
    ],
    "research_gaps": [{"sub_question_id": "sq_N", "what_to_find": "..."}],
    "revision_instructions": "concrete directions for rewriting, or empty if accepting"
}

Be specific and quote the report. A vague complaint cannot be acted on. If the report is genuinely good, accept it — inventing defects to seem rigorous is its own failure."""


def _build_critic_context(
    state: ResearchState,
    mechanical_defects: list[ReportDefect],
    config: Config,
) -> str:
    """Assemble what the critic needs to judge the report."""
    from src.orchestrator.evolution import active_claims_for

    parts = [
        f"# Original question\n{state.query}\n",
        f"# Clarified question\n{state.plan.clarified_query or state.query}\n"
        if state.plan else "",
        "# Sub-questions and their evidence\n",
    ]

    if state.plan:
        for sq in state.plan.sub_questions:
            active = active_claims_for(state, sq.sq_id)
            avg_conf = (
                sum(c.confidence or 0.0 for c in active) / len(active)
                if active else 0.0
            )
            parts.append(
                f"- [{sq.sq_id}] {sq.question}\n"
                f"    {len(active)} surviving claims, avg confidence {avg_conf:.2f}, "
                f"{sq.rounds_used} retrieval round(s)\n"
            )

    parts.append("\n# Claims the report was built from\n")
    for sq in (state.plan.sub_questions if state.plan else []):
        active = active_claims_for(state, sq.sq_id)
        if not active:
            parts.append(f"\n## [{sq.sq_id}] — NO SURVIVING CLAIMS\n")
            continue
        parts.append(f"\n## [{sq.sq_id}]\n")
        for c in active:
            parts.append(f"- (confidence {c.confidence or 0.0:.2f}) {c.text}\n")

    if state.contradictions:
        parts.append(f"\n# Detected contradictions ({len(state.contradictions)})\n")
        for con in state.contradictions:
            parts.append(f"- {con.description} [{con.source_a} vs {con.source_b}]\n")

    if mechanical_defects:
        parts.append("\n# Defects already found by automated checks\n")
        parts.append("(These are confirmed by deterministic checks, not guesses. "
                     "Treat them as established and focus your own analysis elsewhere.)\n")
        for d in mechanical_defects:
            sq_str = f" [{d.sub_question_id}]" if d.sub_question_id else ""
            parts.append(f"- ({d.severity}) {d.defect_type}{sq_str}: {d.detail}\n")

    parts.append(f"\n# The report (version {state.report_version})\n\n{state.report or '(empty)'}\n")
    return "".join(parts)


def critique_report(
    state: ResearchState,
    mechanical_defects: list[ReportDefect],
    llm,
    config: Config,
    pass_num: int = 1,
) -> ReportCritique:
    """
    Run one report-level critique. Returns the critique; does not act on it —
    src/orchestrator/report_loop.py decides what to do with the verdict.
    """
    context = _build_critic_context(state, mechanical_defects, config)

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=CRITIC_SYSTEM,
            user=f"{context}\n\nReview this report. Does it answer the original question?",
        )

    verdict = result.get("verdict", "accept")
    if verdict not in ("accept", "revise_report", "needs_more_research"):
        verdict = "accept"

    defects = list(mechanical_defects)  # mechanical findings are already proven
    for d in result.get("defects", []) or []:
        if not isinstance(d, dict):
            continue
        dtype = str(d.get("defect_type", "") or "unspecified")
        detail = str(d.get("detail", "") or "")[:500]
        if not detail:
            continue
        severity = d.get("severity", "medium")
        if severity not in ("high", "medium", "low"):
            severity = "medium"
        sq_id = d.get("sub_question_id")
        defects.append(ReportDefect(
            defect_type=dtype, detail=detail,
            sub_question_id=sq_id if isinstance(sq_id, str) else None,
            severity=severity, found_by="critic",
        ))

    valid_sq_ids = (
        {sq.sq_id for sq in state.plan.sub_questions} if state.plan else set()
    )
    gaps = []
    for g in result.get("research_gaps", []) or []:
        if not isinstance(g, dict):
            continue
        sq_id = g.get("sub_question_id")
        what = str(g.get("what_to_find", "") or "")[:400]
        # A gap naming a sub-question that doesn't exist can't be acted on.
        if isinstance(sq_id, str) and sq_id in valid_sq_ids and what:
            gaps.append(ResearchGap(sub_question_id=sq_id, what_to_find=what))

    # A "needs_more_research" verdict with no actionable gap is not actionable;
    # downgrade rather than triggering an expensive reopen with no direction.
    if verdict == "needs_more_research" and not gaps:
        verdict = "revise_report"

    critique = ReportCritique(
        pass_num=pass_num,
        report_version=state.report_version,
        answers_the_question=bool(result.get("answers_the_question", True)),
        verdict=verdict,
        defects=defects,
        research_gaps=gaps,
        revision_instructions=str(result.get("revision_instructions", "") or "")[:2000],
        critic_model=getattr(llm, "model", "unknown"),
    )

    high = sum(1 for d in defects if d.severity == "high")
    log_step(
        state, component="report_critic", step="critique",
        input_summary=f"report v{state.report_version}, pass {pass_num}",
        output_summary=(
            f"verdict={verdict}, {len(defects)} defects ({high} high), "
            f"{len(gaps)} research gaps, answers_question={critique.answers_the_question}"
        ),
        latency_ms=timer.ms, cost_tokens=resp.total_tokens,
        metadata={
            "pass": pass_num, "verdict": verdict,
            "n_defects": len(defects), "n_high": high, "n_gaps": len(gaps),
            "defect_types": [d.defect_type for d in defects],
            "critic_model": critique.critic_model,
        },
    )

    return critique
