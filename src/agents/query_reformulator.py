"""
Query reformulator — makes retrieval round N+1 search for something *different*
from round N.

THE PROBLEM. `research_sub_question` originally passed `sq.question` verbatim to
both arXiv and web search on every single round. Round 3 of retrieval for a
sub-question issued the identical query round 1 did. Extra rounds therefore only
paged deeper into the same ranked result list — they added evidence *volume*,
not new *angles*. That is a large part of why the 7-test-case multiround
evaluation found that accumulating rounds barely moved confidence or difficulty: the second round largely re-confirmed
what the first already said, because it asked the same question.

This is the Search-R1 idea in its cheapest useful form: the agent should reason
about what its previous searches actually returned, notice what is still
missing, and issue a query targeted at the gap.

CONTEXT COMPACTION. The reformulator is deliberately NOT given the accumulated
raw evidence pool — that pool runs to hundreds of chunks and grows every round,
so feeding it in would make each successive reformulation more expensive than
the retrieval it is trying to improve. Instead it sees a compact digest built
from `SubQuestion.retrieval_attempts`: which queries were already tried, which
source titles came back, and how the resulting claims scored. A short structured
summary of "what we did and what it got us" is both cheaper and easier for the
model to act on than the underlying text it summarizes.

The reformulated query is recorded back onto the sub-question, so the next round
can see it too, and so the whole search trajectory is auditable in state.json
rather than being invisible inside a loop.
"""

from __future__ import annotations

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import ResearchState, SubQuestion


REFORMULATOR_SYSTEM = """You are directing a literature search. An earlier search for this sub-question already ran and returned the sources listed below, but the evidence is still incomplete or unconvincing.

Your job: write a DIFFERENT search query that targets what the previous searches missed.

Rules:
- Do NOT restate the original sub-question. That exact query has already been run, and re-running it returns the same sources.
- Look at what the previous queries returned and identify the specific gap: a sub-topic with no coverage, a claim asserted by only one source that needs corroboration, a counter-position nobody addressed, a technical term the earlier phrasing missed.
- Prefer concrete technical terminology over general phrasing — search engines reward specific method names, metric names, and named approaches over conversational questions.
- If earlier results were off-topic (e.g. dictionary definitions, unrelated domains), diagnose why the phrasing misfired and pick wording that disambiguates.
- The query should be a search query, not a sentence. Keep it under 20 words.

Respond as JSON: {
    "gap": "one sentence: what is missing from the evidence gathered so far",
    "query": "the new search query",
    "rationale": "one sentence: why this query targets that gap"
}"""


def _build_digest(sq: SubQuestion, state: ResearchState) -> str:
    """
    Compact summary of prior retrieval for this sub-question.

    This is the compaction step: prior rounds are represented by their queries,
    the source titles they returned, and how well the resulting claims held up —
    never by the raw chunk text, which is what makes it cheap enough to run
    every round.
    """
    lines = []
    for attempt in sq.retrieval_attempts:
        titles = attempt.source_titles[:6]
        title_str = "; ".join(titles) if titles else "(nothing usable)"
        lines.append(
            f"- Round {attempt.round_num} searched: \"{attempt.query}\"\n"
            f"    returned {attempt.n_chunks} chunks from: {title_str}"
        )

    # How did the claims from those rounds actually fare? Low-confidence or
    # retracted claims are the strongest signal about where evidence is thin.
    sq_claims = [c for c in state.claims if c.sub_question_id == sq.sq_id]
    active = [c for c in sq_claims if c.is_active]
    if sq_claims:
        avg_conf = sum(c.confidence or 0.0 for c in active) / max(1, len(active))
        retracted = len(sq_claims) - len(active)
        lines.append(
            f"- Claims so far: {len(active)} standing "
            f"(avg confidence {avg_conf:.2f}), {retracted} retracted as unsupported."
        )
        weak = sorted(active, key=lambda c: c.confidence or 0.0)[:3]
        if weak:
            lines.append("- Weakest standing claims (these need corroboration):")
            for c in weak:
                lines.append(f"    [{c.confidence or 0.0:.2f}] {c.text[:150]}")

    return "\n".join(lines) if lines else "(no prior retrieval recorded)"


def reformulate_query(
    state: ResearchState,
    sq: SubQuestion,
    round_num: int,
    llm,
    config: Config,
) -> tuple[str, str, str]:
    """
    Produce the search query for `round_num`.

    Returns (query, rationale, gap). Round 1 (or reformulation disabled, or no
    recorded history) uses the sub-question verbatim — there is nothing to learn
    from yet.
    """
    if (
        not config.retrieval.reformulate_queries
        or round_num <= 1
        or not sq.retrieval_attempts
        or llm is None
    ):
        return sq.question, "", ""

    digest = _build_digest(sq, state)

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=REFORMULATOR_SYSTEM,
            user=(
                f"Sub-question being researched: {sq.question}\n\n"
                f"What previous rounds already did:\n{digest}\n\n"
                f"Write the search query for round {round_num}."
            ),
        )

    query = str(result.get("query", "") or "").strip()
    rationale = str(result.get("rationale", "") or "")[:300]
    gap = str(result.get("gap", "") or "")[:300]

    # Guard against the two degenerate outputs: an empty query, or the model
    # handing back the sub-question it was explicitly told not to reuse.
    tried = {a.query.strip().lower() for a in sq.retrieval_attempts}
    if not query or query.strip().lower() in tried:
        log_step(
            state, component="query_reformulator", step="fallback",
            input_summary=f"SQ: {sq.question[:60]}, round {round_num}",
            output_summary="reformulation unusable (empty or duplicate) — reusing sub-question",
            latency_ms=timer.ms, cost_tokens=resp.total_tokens,
            metadata={"sq_id": sq.sq_id, "round": round_num},
        )
        return sq.question, "", gap

    log_step(
        state, component="query_reformulator", step="reformulate",
        input_summary=f"SQ: {sq.question[:60]}, round {round_num}",
        output_summary=f"gap={gap[:80]} | new query: {query[:80]}",
        latency_ms=timer.ms, cost_tokens=resp.total_tokens,
        metadata={
            "sq_id": sq.sq_id, "round": round_num,
            "query": query, "gap": gap, "rationale": rationale,
        },
    )

    return query, rationale, gap
