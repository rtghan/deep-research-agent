"""
Planner — decomposes a query into a research plan of sub-questions.

Design decision: the planner uses an LLM to break a complex query into 3-5
atomic sub-questions. Each sub-question should be independently answerable
from external sources. This decomposition is what enables:
- Track A: per-sub-question difficulty estimation and adaptive compute
- Track B: per-claim verification against specific sub-question evidence

Why not search directly on the full query? A monolithic query produces
monolithic evidence, which makes claim-level verification nearly impossible.
Decomposition creates the structure that both depth tracks depend on.
"""

from __future__ import annotations

import json

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import ResearchPlan, ResearchState, SubQuestion
from src.tools.base import LLMClient


PLANNER_SYSTEM = """You are a research planner. Your job is to break a research query into 3-5 atomic sub-questions.

Rules:
- Each sub-question must be independently answerable from external sources (arXiv, web).
- Sub-questions should collectively cover the full scope of the original query.
- Avoid overlap — don't ask the same thing twice.
- Be specific enough that a search engine can find relevant results.

Respond as JSON: {"clarified_query": "rewritten version of the query for clarity", "sub_questions": [{"question": "..."}, ...]}"""


def plan(state: ResearchState, llm: LLMClient, config: Config) -> None:
    """Decompose the query into sub-questions and store the plan in state."""
    with Timer() as timer:
        result, resp = llm.complete_json(
            system=PLANNER_SYSTEM,
            user=f"Research query: {state.query}\n\nBreak this into 3-5 sub-questions.",
        )

    sub_questions = []
    for i, sq in enumerate(result.get("sub_questions", [])):
        question = sq.get("question", sq) if isinstance(sq, dict) else str(sq)
        sub_questions.append(SubQuestion(
            sq_id=f"sq_{i}",
            question=question,
        ))

    state.plan = ResearchPlan(
        query=state.query,
        sub_questions=sub_questions,
        clarified_query=result.get("clarified_query", state.query),
    )

    log_step(
        state,
        component="planner",
        step="decompose",
        input_summary=f"Query: {state.query[:100]}",
        output_summary=f"{len(sub_questions)} sub-questions",
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
        metadata={"sub_questions": [sq.question for sq in sub_questions]},
    )
