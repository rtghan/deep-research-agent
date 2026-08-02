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

from pathlib import Path

from src.agents.extractor import extract_claims
from src.agents.planner import plan
from src.agents.researcher import research_sub_question
from src.agents.synthesizer import synthesize
from src.obs.trace import Timer, log_step, save_trace, save_full_state
from src.orchestrator.allocator import allocate_budget, should_continue
from src.orchestrator.config import Config
from src.orchestrator.evolution import active_claims_for, evolve_claims
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
        # from the extractor is the whole point (DECISIONS.md D021).
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

    # Phase 1: Plan
    log_step(state, "pipeline", "start", f"Query: {query[:100]}", "Starting pipeline")
    plan(state, sub_llm, config)
    if not state.plan or not state.plan.sub_questions:
        state.report = "# Research Report\n\nFailed to decompose query into sub-questions."
        return state

    # Phase 2: Per-sub-question research loop
    for sq in state.plan.sub_questions:
        # Track A: estimate difficulty and allocate budget
        estimate_difficulty(state, sq, config)
        allocate_budget(state, sq, config)

        # Research loop: retrieve → extract → verify → confidence → maybe loop
        while sq.rounds_used < sq.compute_budget:
            round_num = sq.rounds_used + 1
            new_evidence = research_sub_question(state, sq, round_num, config)

            if not new_evidence:
                # No new evidence found — stop early
                sq.sufficient_evidence = False
                break

            # Extract claims from new evidence
            new_claims = extract_claims(state, sq, new_evidence, sub_llm, config)

            if new_claims:
                # Verify and score
                verify_claims(state, new_claims, sub_llm, config)
                score_confidence(state, new_claims, config)

                # Claim evolution: challenge EVERY active claim for this
                # sub-question — including ones written in earlier rounds —
                # against the full accumulated evidence pool. This is what lets
                # this round's evidence rewrite an earlier round's claim
                # instead of merely sitting next to it.
                evolve_claims(
                    state, sq,
                    challenger_llm=challenger_llm,
                    reviser_llm=reviser_llm,
                    verifier_llm=sub_llm,
                    config=config,
                    round_num=round_num,
                    # Reuses the challenger client for judging: it is already
                    # independent from the extractor (DECISIONS.md D021), so no
                    # third API client is needed for this second quality signal.
                    judge_llm=challenger_llm,
                )

                # Re-score every surviving claim: revisions changed the text,
                # the support scores, and the reasoning scores.
                sq_claims = active_claims_for(state, sq.sq_id)
                if sq_claims:
                    score_confidence(state, sq_claims, config)

                # Update difficulty based on confidence
                update_difficulty(state, sq, new_claims, config)

                # Feedback loop — should we continue?
                if config.adaptive.enabled:
                    if not should_continue(state, sq, new_claims, config):
                        break  # confidence is high enough, stop spending compute
                else:
                    # Uniform mode: always use full budget
                    if sq.rounds_used >= sq.compute_budget:
                        break

    # Phase 3: Cross-source contradiction detection
    # Retracted claims are excluded — a claim the system already withdrew should
    # not go on to generate contradictions or drag down its counterpart's score.
    surviving_claims = [c for c in state.claims if c.is_active]
    detect_contradictions(state, surviving_claims, sub_llm, config)

    # Re-score confidence with contradiction info
    score_confidence(state, surviving_claims, config)

    # Phase 4: Synthesize report
    synthesize(state, synth_llm, config)

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
        },
    )

    # Save outputs
    out = Path(output_dir)
    save_trace(state, out / "trace.jsonl")
    save_full_state(state, out / "state.json")
    with open(out / "report.md", "w") as f:
        f.write(state.report or "")

    return state
