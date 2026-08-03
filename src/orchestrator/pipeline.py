"""
Pipeline orchestrator — the main entry point that runs the full research agent.

This is the pipeline:
1. Planner: decompose query → sub-questions
2. For each sub-question:
   a. Difficulty estimator: estimate difficulty
   b. Allocator: assign compute budget
   c. Researcher: run retrieval rounds (up to budget)
   d. Extractor: extract claims from evidence
   e. Verifier: verify claims against evidence
   f. Confidence scorer: assign calibrated confidence
   g. Allocator feedback: if confidence low and budget allows, loop back to (c)
3. Verifier: detect cross-source contradictions
4. Synthesizer: produce final report with confidence scores

The adaptive vs. uniform ablation is controlled by config.adaptive.enabled.
When disabled, all sub-questions get the same compute budget (max_budget).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.agents.extractor import extract_claims
from src.agents.planner import plan
from src.agents.researcher import research_sub_question
from src.agents.synthesizer import synthesize
from src.obs.progress import narrate_phase, get_reporter
from src.obs.trace import Timer, log_step, save_trace, save_full_state
from src.orchestrator.allocator import allocate_budget, should_continue
from src.orchestrator.config import Config
from src.orchestrator.evolution import active_claims_for, evolve_claims
from src.orchestrator.report_loop import run_report_correction
from src.orchestrator.scheduler import RoundOutcome, run_scheduled_research
from src.orchestrator.state import ResearchState
from src.scoring.confidence import score_confidence
from src.scoring.difficulty import estimate_difficulty, update_difficulty
from src.scoring.verifier import verify_claims, detect_contradictions
from src.tools.base import LLMClient
from src.tools.mock_llm import MockLLMClient


def run_research(
    query: str,
    config: Config | None = None,
    output_dir: str = "outputs",
    use_mock: bool = False,
) -> ResearchState:
    """
    Run the full deep research pipeline on a query.
    Returns the final ResearchState with report, evidence, claims, and trace.
    """
    if config is None:
        config = Config.load()

    # Create LLM clients (mock mode for testing without API keys)
    if use_mock:
        sub_llm = MockLLMClient(model="mock", temperature=config.llm.temperature, max_tokens=config.llm.max_tokens)
        synth_llm = MockLLMClient(model="mock", temperature=config.llm.temperature, max_tokens=2000)
        # Mock challenger keeps a distinct model name so the self-agreement
        # ablation still reads as "different model" in the trace.
        challenger_llm = MockLLMClient(
            model=config.evolution.challenger_model or "mock-challenger",
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        reviser_llm = sub_llm
    else:
        import os
        api_key = os.environ.get(config.llm.api_key_env, "")
        base_url = config.llm.base_url or None
        sub_llm = LLMClient(
            model=config.llm.sub_step_model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            api_key=api_key,
            base_url=base_url,
        )
        synth_llm = LLMClient(
            model=config.llm.synthesis_model,
            temperature=config.llm.temperature,
            max_tokens=2000,
            api_key=api_key,
            base_url=base_url,
        )
        # The challenger gets its OWN client so it can sit on a different
        # provider entirely, not merely a different model string. Independence
        # from the extractor is the whole point.
        challenger_key = os.environ.get(
            config.evolution.challenger_api_key_env or config.llm.api_key_env, ""
        )
        challenger_llm = LLMClient(
            model=config.evolution.challenger_model or config.llm.sub_step_model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            api_key=challenger_key,
            base_url=(config.evolution.challenger_base_url or config.llm.base_url) or None,
        )
        reviser_llm = (
            sub_llm if not config.evolution.reviser_model
            or config.evolution.reviser_model == config.llm.sub_step_model
            else LLMClient(
                model=config.evolution.reviser_model,
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
                api_key=api_key,
                base_url=base_url,
            )
        )

    state = ResearchState(query=query)
    reporter = get_reporter()

    # Phase 1: Plan
    narrate_phase("Understanding the question", query[:100])
    log_step(state, "pipeline", "start", f"Query: {query[:100]}", "Starting pipeline")
    plan(state, sub_llm, config)
    if not state.plan or not state.plan.sub_questions:
        state.report = "# Research Report\n\nFailed to decompose query into sub-questions."
        return state
    cap = getattr(config.execution, "max_sub_questions", 0)
    if cap and len(state.plan.sub_questions) > cap:
        dropped = len(state.plan.sub_questions) - cap
        state.plan.sub_questions = state.plan.sub_questions[:cap]
        log_step(state, "pipeline", "trim_plan", f"cap={cap}",
                 f"kept {cap} sub-questions, dropped {dropped}",
                 metadata={"cap": cap, "dropped": dropped})
    for sq in state.plan.sub_questions:
        reporter.sub(f"  {sq.sq_id}: {sq.question}")

    # Phase 2: Research. Two strategies share the SAME round body below and
    # differ only in scheduling — which sub-question gets the next round, and
    # when to stop. Keeping the body common is what makes them comparable.
    narrate_phase("Researching each sub-question")

    def run_round(sq, round_num: int) -> RoundOutcome:
        """One full round for one sub-question. Returns what it bought."""
        outcome = RoundOutcome()
        claims_before = {c.claim_id for c in state.claims}

        new_evidence = research_sub_question(state, sq, round_num, config, llm=sub_llm)
        outcome.new_evidence = len(new_evidence)
        if not new_evidence:
            sq.sufficient_evidence = False
            outcome.found_nothing = True
            return outcome

        new_claims = extract_claims(state, sq, new_evidence, sub_llm, config)
        outcome.new_claims = len(new_claims)
        if not new_claims:
            outcome.found_nothing = True
            return outcome

        verify_claims(state, new_claims, sub_llm, config)
        score_confidence(state, new_claims, config)

        # Claim evolution: challenge EVERY active claim for this sub-question —
        # including ones written in earlier rounds — against the full
        # accumulated evidence pool. This is what lets this round's evidence
        # rewrite an earlier round's claim instead of merely sitting next to it.
        evolve_claims(
            state, sq,
            challenger_llm=challenger_llm,
            reviser_llm=reviser_llm,
            verifier_llm=sub_llm,
            config=config,
            round_num=round_num,
            # Reuses the challenger client for judging: it is already
            # independent from the extractor, so no
            # third API client is needed for this second quality signal.
            judge_llm=challenger_llm,
        )

        # Yield is measured on PRE-EXISTING claims only: newly extracted claims
        # always look like activity, which would double-count the round's own
        # output as evidence that the round was worthwhile.
        outcome.claims_changed = sum(
            1 for c in state.claims
            if c.claim_id in claims_before
            and any(r.round_num == round_num for r in c.revisions)
        )

        sq_claims = active_claims_for(state, sq.sq_id)
        if sq_claims:
            score_confidence(state, sq_claims, config)
        update_difficulty(state, sq, new_claims, config)
        return outcome

    for sq in state.plan.sub_questions:
        estimate_difficulty(state, sq, config)

    # The scheduler re-ranks after every round to choose where the next one
    # goes, so it is inherently sequential and cannot be fanned out without
    # giving up the adaptivity that justifies it.
    parallel = (
        config.execution.parallel_sub_questions
        and not (config.adaptive.enabled and config.adaptive.strategy == "scheduler")
        and len(state.plan.sub_questions) > 1
    )

    if config.adaptive.enabled and config.adaptive.strategy == "scheduler":
        # Global pool, allocated by ranking. See scheduler.py for why this
        # exists: the threshold strategy provably could not grant a 3rd round.
        run_scheduled_research(state, config, run_round)
    else:
        # Original strategy: per-sub-question budget from an absolute threshold.
        # The body below is identical whether it runs serially or in a worker
        # thread — sub-questions share no state, which is what makes fanning
        # them out safe (see ExecutionConfig).
        def research_sub_question_fully(sq) -> None:
            buffered = parallel  # only buffer narration when output would interleave
            if buffered:
                reporter.begin_buffered()
            try:
                reporter.push(f"[{sq.sq_id}] {sq.question}")
                allocate_budget(state, sq, config)

                while sq.rounds_used < sq.compute_budget:
                    round_num = sq.rounds_used + 1
                    outcome = run_round(sq, round_num)
                    if outcome.found_nothing:
                        break

                    if config.adaptive.enabled:
                        if not should_continue(state, sq, None, config):
                            n_active = len(active_claims_for(state, sq.sq_id))
                            avg = (
                                sum(c.confidence or 0 for c in active_claims_for(state, sq.sq_id))
                                / max(1, n_active)
                            )
                            reporter.decision(
                                "Stopping research on this sub-question",
                                f"{n_active} claims at {avg:.2f} average confidence — "
                                f"enough to answer it",
                            )
                            break
                    elif sq.rounds_used >= sq.compute_budget:
                        break
                reporter.pop()
            finally:
                if buffered:
                    reporter.flush_buffered()

        if parallel:
            workers = min(config.execution.max_workers, len(state.plan.sub_questions))
            log_step(
                state, "pipeline", "parallel_start",
                f"{len(state.plan.sub_questions)} sub-questions",
                f"running with {workers} workers",
                metadata={"workers": workers},
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(research_sub_question_fully, sq): sq
                    for sq in state.plan.sub_questions
                }
                for fut in as_completed(futures):
                    sq = futures[fut]
                    # One sub-question failing should not lose the other four.
                    # Record it and carry on; the report will show the gap.
                    try:
                        fut.result()
                    except Exception as exc:
                        sq.sufficient_evidence = False
                        log_step(
                            state, "pipeline", "sub_question_failed",
                            f"{sq.sq_id}: {sq.question[:60]}",
                            f"{type(exc).__name__}: {exc}",
                            metadata={"sq_id": sq.sq_id, "error": type(exc).__name__},
                        )
        else:
            for sq in state.plan.sub_questions:
                research_sub_question_fully(sq)

    # Phase 3: Cross-source contradiction detection
    # Retracted claims are excluded — a claim the system already withdrew should
    # not go on to generate contradictions or drag down its counterpart's score.
    narrate_phase("Comparing evidence across sources")
    surviving_claims = [c for c in state.claims if c.is_active]
    detect_contradictions(state, surviving_claims, sub_llm, config)

    # Re-score confidence with contradiction info
    score_confidence(state, surviving_claims, config)

    # Phase 4: Synthesize report
    narrate_phase("Writing the report")
    synthesize(state, synth_llm, config)

    narrate_phase("Reviewing my own report")
    # Phase 5: Report-level self-correction. Everything above improves
    # individual claims; this is the only stage that asks whether the assembled
    # report answers the question that was actually asked, and reopens
    # retrieval if the evidence base itself turns out to be inadequate.
    run_report_correction(
        state,
        synth_llm=synth_llm,
        # Independent critic: reuses the challenger's client, which is already
        # a different model from the synthesizer.
        critic_llm=challenger_llm,
        sub_llm=sub_llm,
        challenger_llm=challenger_llm,
        reviser_llm=reviser_llm,
        config=config,
    )

    # Phase 3 outputs can change if Phase 5 reopened research.
    surviving_claims = [c for c in state.claims if c.is_active]

    revised = sum(1 for c in state.claims if c.revisions)
    retracted = sum(1 for c in state.claims if c.status == "retracted")
    log_step(
        state, "pipeline", "end",
        f"Query: {query[:100]}",
        f"Report: {len(state.report or '')} chars, {len(surviving_claims)} claims "
        f"({revised} revised, {retracted} retracted), {len(state.contradictions)} contradictions",
        metadata={
            "total_tokens": state.total_tokens,
            "total_latency_ms": state.total_latency_ms,
            "total_evidence": len(state.evidence),
            "claims_revised": revised,
            "claims_retracted": retracted,
            "challenges_issued": len(state.challenges),
            "report_version": state.report_version,
            "report_critiques": len(state.report_critiques),
        },
    )

    # Save outputs
    out = Path(output_dir)
    save_trace(state, out / "trace.jsonl")
    save_full_state(state, out / "state.json")
    with open(out / "report.md", "w") as f:
        f.write(state.report or "")

    return state
