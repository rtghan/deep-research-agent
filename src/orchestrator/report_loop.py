"""
Report-level self-correction loop — critique the report, then fix it.

Phase 5 of the pipeline. Everything before this improves individual claims; this
is the only stage that asks whether the assembled report answers the question
that was actually asked.

    synthesize → mechanical checks → critic → { accept
                                             | revise_report      (re-synthesize)
                                             | needs_more_research (reopen retrieval) }

TIER 1: MECHANICAL CHECKS (this module, no LLM)
-----------------------------------------------
Several report-level defects are decidable in code, and code is both cheaper and
more reliable than asking a model. A retracted claim whose text still appears in
the report is not a judgment call — it is a substring match and a hard error.
So is a sub-question with zero surviving claims that the report never flags as a
gap. These run first, and their findings are handed to the LLM critic as
established facts, so the model spends its attention on the genuinely
judgment-dependent defects (overstatement, burial, "does this answer the
question") rather than re-deriving what a substring check already proved.

TIER 2: THE CRITIC (src/agents/report_critic.py, independent model)

LOOP CONTROL
------------
Three independent brakes, because a self-correction loop that can reopen
retrieval is the most expensive thing in the pipeline and the easiest to make
run forever:

1. `max_passes` — hard cap on correction passes.
2. One reopen per sub-question, ever. A sub-question that has already been
   re-researched can subsequently only be fixed by rewriting. Without this, a
   critic that stays unhappy about one hard sub-question re-researches it every
   pass.
3. `stop_when_not_improving` — if a pass does not reduce the high-severity
   defect count, stop. The 7-test-case evaluation established that a critic
   finding *more* fault is not evidence it is right, so
   "keeps complaining" must terminate the loop rather than justify another pass.
"""

from __future__ import annotations

import re

from src.agents.report_critic import critique_report
from src.agents.synthesizer import synthesize
from src.obs.trace import log_step
from src.orchestrator.config import Config
from src.orchestrator.evolution import active_claims_for
from src.orchestrator.state import ReportDefect, ResearchState


def _significant_words(text: str, min_len: int = 5) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= min_len]


def _text_appears_in_report(claim_text: str, report: str) -> bool:
    """
    Heuristic: does the report still assert this (retracted) claim?

    Exact substring matching is useless here — the synthesizer rewords claims
    into prose. Instead: take the claim's distinctive words and check whether a
    high proportion of them co-occur in some paragraph of the report. Tuned
    conservatively (0.8) because a false positive here accuses the report of a
    hard error it may not have committed.
    """
    words = set(_significant_words(claim_text))
    if len(words) < 4:
        return False
    report_lower = report.lower()
    for para in report_lower.split("\n\n"):
        if not para.strip():
            continue
        para_words = set(_significant_words(para))
        if not para_words:
            continue
        overlap = len(words & para_words) / len(words)
        if overlap >= 0.8:
            return True
    return False


def mechanical_checks(state: ResearchState, config: Config) -> list[ReportDefect]:
    """
    Deterministic, LLM-free defect detection. See module docstring for why this
    tier exists separately from the critic.
    """
    defects: list[ReportDefect] = []
    report = state.report or ""
    report_lower = report.lower()

    if not report or not state.plan:
        return defects

    # 1. HARD ERROR: a claim the system retracted still being asserted.
    for claim in state.claims:
        if claim.status != "retracted":
            continue
        if _text_appears_in_report(claim.text or claim.original_text or "", report):
            defects.append(ReportDefect(
                defect_type="retracted_claim_cited",
                detail=(
                    f"The report still appears to assert a claim that was retracted "
                    f"during verification: \"{(claim.text or claim.original_text or '')[:200]}\". "
                    f"Retracted claims must not appear as findings."
                ),
                sub_question_id=claim.sub_question_id,
                severity="high", found_by="mechanical",
            ))

    # 2. A sub-question with no surviving claims that the report never flags.
    gap_section_present = any(
        marker in report_lower for marker in ("known gap", "limitation", "insufficient evidence")
    )
    for sq in state.plan.sub_questions:
        active = active_claims_for(state, sq.sq_id)
        if not active:
            defects.append(ReportDefect(
                defect_type="missing_coverage",
                detail=(
                    f"Sub-question \"{sq.question}\" has no surviving claims"
                    + ("" if gap_section_present else
                       " and the report has no gaps/limitations section acknowledging it")
                    + "."
                ),
                sub_question_id=sq.sq_id,
                severity="high" if not gap_section_present else "medium",
                found_by="mechanical",
            ))
            continue

        # 3. Thin evidence: surviving claims, but weak ones.
        avg_conf = sum(c.confidence or 0.0 for c in active) / len(active)
        if avg_conf < config.report_correction.thin_confidence_threshold:
            defects.append(ReportDefect(
                defect_type="missing_coverage",
                detail=(
                    f"Sub-question \"{sq.question}\" is supported only by low-confidence "
                    f"claims (average {avg_conf:.2f}). The report should either hedge these "
                    f"explicitly or acknowledge the gap."
                ),
                sub_question_id=sq.sq_id, severity="medium", found_by="mechanical",
            ))

    # 4. Contradictions detected but no section discussing them.
    if state.contradictions and "contradict" not in report_lower and "disagree" not in report_lower:
        defects.append(ReportDefect(
            defect_type="mishandled_contradiction",
            detail=(
                f"{len(state.contradictions)} cross-source contradiction(s) were detected, "
                f"but the report never mentions contradictions or disagreement."
            ),
            severity="high", found_by="mechanical",
        ))

    if defects:
        log_step(
            state, component="report_loop", step="mechanical_checks",
            input_summary=f"report v{state.report_version}",
            output_summary=f"{len(defects)} defect(s) found without an LLM call",
            latency_ms=0, cost_tokens=0,
            metadata={"defect_types": [d.defect_type for d in defects]},
        )

    return defects


def _reopen_research(
    state: ResearchState,
    critique,
    sub_llm,
    challenger_llm,
    reviser_llm,
    config: Config,
    already_reopened: set[str],
) -> bool:
    """
    Act on the critic's research gaps: run another targeted retrieval round on
    the named sub-questions, seeded with what the critic said was missing.

    Returns True if any new evidence was actually gathered.
    """
    from src.agents.extractor import extract_claims
    from src.agents.researcher import research_sub_question
    from src.orchestrator.evolution import evolve_claims
    from src.scoring.confidence import score_confidence
    from src.scoring.verifier import verify_claims

    if not state.plan:
        return False

    sq_by_id = {sq.sq_id: sq for sq in state.plan.sub_questions}
    made_progress = False

    for gap in critique.research_gaps:
        sq = sq_by_id.get(gap.sub_question_id)
        if sq is None or sq.sq_id in already_reopened:
            continue
        already_reopened.add(sq.sq_id)

        # THE JOIN WITH QUERY REFORMULATION: write the critic's "what to find"
        # into the sub-question's retrieval history as the outstanding gap. The
        # reformulator reads retrieval_attempts to build the next query, so the
        # re-research round searches for what the CRITIC said was missing rather
        # than blindly re-running the original sub-question.
        if sq.retrieval_attempts:
            sq.retrieval_attempts[-1].gap_noted = gap.what_to_find

        sq.compute_budget = sq.rounds_used + 1
        round_num = sq.rounds_used + 1

        log_step(
            state, component="report_loop", step="reopen_research",
            input_summary=f"{sq.sq_id}: {sq.question[:60]}",
            output_summary=f"round {round_num} targeting: {gap.what_to_find[:100]}",
            latency_ms=0, cost_tokens=0,
            metadata={"sq_id": sq.sq_id, "what_to_find": gap.what_to_find},
        )

        new_evidence = research_sub_question(state, sq, round_num, config, llm=sub_llm)
        if not new_evidence:
            continue

        new_claims = extract_claims(state, sq, new_evidence, sub_llm, config)
        if not new_claims:
            continue

        made_progress = True
        verify_claims(state, new_claims, sub_llm, config)
        score_confidence(state, new_claims, config)
        evolve_claims(
            state, sq,
            challenger_llm=challenger_llm, reviser_llm=reviser_llm,
            verifier_llm=sub_llm, config=config, round_num=round_num,
            judge_llm=challenger_llm,
        )
        sq_claims = active_claims_for(state, sq.sq_id)
        if sq_claims:
            score_confidence(state, sq_claims, config)

    return made_progress


def run_report_correction(
    state: ResearchState,
    synth_llm,
    critic_llm,
    sub_llm,
    challenger_llm,
    reviser_llm,
    config: Config,
) -> None:
    """
    Phase 5: critique the report and correct it, up to `max_passes` times.

    Mutates state.report / state.report_version in place and appends a
    ReportCritique per pass.
    """
    rc = config.report_correction
    if not rc.enabled or not state.report:
        return

    already_reopened: set[str] = set()
    prev_high_count: int | None = None

    for pass_num in range(1, rc.max_passes + 1):
        mech = mechanical_checks(state, config)
        critique = critique_report(state, mech, critic_llm, config, pass_num=pass_num)
        state.report_critiques.append(critique)

        high_count = sum(1 for d in critique.defects if d.severity == "high")

        if critique.verdict == "accept":
            critique.action_taken = "none"
            log_step(
                state, component="report_loop", step="accept",
                input_summary=f"pass {pass_num}",
                output_summary=f"report accepted at v{state.report_version}",
                latency_ms=0, cost_tokens=0,
            )
            break

        # Convergence brake: a pass that didn't reduce high-severity defects
        # means the loop is not making progress, whatever the critic says.
        if (
            rc.stop_when_not_improving
            and prev_high_count is not None
            and high_count >= prev_high_count
        ):
            critique.action_taken = "none"
            log_step(
                state, component="report_loop", step="stop_not_improving",
                input_summary=f"pass {pass_num}",
                output_summary=(
                    f"high-severity defects {prev_high_count} -> {high_count}; "
                    f"not converging, stopping"
                ),
                latency_ms=0, cost_tokens=0,
            )
            break
        prev_high_count = high_count

        reopened = False
        if critique.verdict == "needs_more_research" and rc.allow_research_reopen:
            reopened = _reopen_research(
                state, critique, sub_llm, challenger_llm, reviser_llm,
                config, already_reopened,
            )

        # Re-synthesize either way: after reopening (new claims to include) or
        # as the fix itself when the defects were presentational.
        synthesize(state, synth_llm, config, critique=critique)
        state.report_version += 1
        critique.action_taken = "reopened_research" if reopened else "revised"

        log_step(
            state, component="report_loop", step="revise",
            input_summary=f"pass {pass_num}, verdict={critique.verdict}",
            output_summary=(
                f"report v{state.report_version - 1} -> v{state.report_version} "
                f"({critique.action_taken}, {len(critique.defects)} defects addressed)"
            ),
            latency_ms=0, cost_tokens=0,
            metadata={"pass": pass_num, "action": critique.action_taken},
        )
