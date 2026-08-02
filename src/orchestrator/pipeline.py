"""
Pipeline orchestrator — the main entry point that runs the full research agent.

This is the A+B hybrid pipeline:
1. Planner: decompose query → sub-questions
2. For each sub-question:
   a. Difficulty estimator: estimate difficulty (Track A)
   b. Allocator: assign compute budget (Track A)
   c. Researcher: run retrieval rounds (up to budget)
   d. Extractor: extract claims from evidence
   e. Verifier: verify claims against evidence (Track B)
   f. Confidence scorer: assign calibrated confidence (Track B)
   g. Allocator feedback: if confidence low and budget allows, loop back to (c)
3. Verifier: detect cross-source contradictions (Track B)
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
                # Track B: verify and score
                verify_claims(state, new_claims, sub_llm, config)
                score_confidence(state, new_claims, config)

                # Track A: update difficulty based on confidence
                update_difficulty(state, sq, new_claims, config)

                # Track A: feedback loop — should we continue?
                if config.adaptive.enabled:
                    if not should_continue(state, sq, new_claims, config):
                        break  # confidence is high enough, stop spending compute
                else:
                    # Uniform mode: always use full budget
                    if sq.rounds_used >= sq.compute_budget:
                        break

    # Phase 3: Cross-source contradiction detection
    detect_contradictions(state, state.claims, sub_llm, config)

    # Re-score confidence with contradiction info
    score_confidence(state, state.claims, config)

    # Phase 4: Synthesize report
    synthesize(state, synth_llm, config)

    log_step(
        state, "pipeline", "end",
        f"Query: {query[:100]}",
        f"Report: {len(state.report or '')} chars, {len(state.claims)} claims, {len(state.contradictions)} contradictions",
        metadata={
            "total_tokens": state.total_tokens,
            "total_latency_ms": state.total_latency_ms,
            "total_evidence": len(state.evidence),
        },
    )

    # Save outputs
    out = Path(output_dir)
    save_trace(state, out / "trace.jsonl")
    save_full_state(state, out / "state.json")
    with open(out / "report.md", "w") as f:
        f.write(state.report or "")

    return state
