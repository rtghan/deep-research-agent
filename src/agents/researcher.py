"""
Researcher — retrieves evidence for a sub-question from external sources.

Design decision: the researcher runs retrieval rounds. In each round it:
1. Searches arXiv (always available) and web (DuckDuckGo by default, no key needed)
2. Chunks the retrieved text into evidence pieces with provenance
3. Appends evidence to state

The number of rounds is controlled by the adaptive allocator.
In uniform mode, every sub-question gets the same budget.
In adaptive mode, harder sub-questions get more rounds.

This is where "test-time compute" is spent — each round = more search +
more LLM calls for extraction. The cost-quality curve measures this.
"""

from __future__ import annotations

from src.agents.query_reformulator import reformulate_query
from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import (
    EvidenceChunk,
    ResearchState,
    RetrievalAttempt,
    SubQuestion,
)
from src.retrieval.chunker import chunk_text
from src.tools.arxiv import search_arxiv, fetch_arxiv_content, fetch_arxiv_content_fast
from src.tools.search import web_search


def research_sub_question(
    state: ResearchState,
    sq: SubQuestion,
    round_num: int,
    config: Config,
    llm=None,
) -> list[EvidenceChunk]:
    """Run one retrieval round for a sub-question. Returns new evidence chunks."""
    new_evidence: list[EvidenceChunk] = []
    search_results_per_query = config.retrieval.search_results_per_query

    # Round 2+ searches for what earlier rounds MISSED rather than re-issuing
    # the sub-question verbatim (which only pages deeper into the same results).
    query, reformulation_rationale, gap = reformulate_query(
        state, sq, round_num, llm, config
    )

    # Search arXiv
    with Timer() as timer:
        papers = search_arxiv(query, max_results=search_results_per_query)

    chunk_id_base = f"{sq.sq_id}_r{round_num}"
    fetch = (fetch_arxiv_content if config.retrieval.fetch_fulltext
             else fetch_arxiv_content_fast)
    for i, paper in enumerate(papers):
        text = fetch(paper)
        if text:
            chunks = chunk_text(
                text=text,
                source_url=paper.url,
                source_title=paper.title,
                source_type="arxiv",
                chunk_size=config.retrieval.chunk_size,
                chunk_overlap=config.retrieval.chunk_overlap,
                retrieval_round=round_num,
                chunk_id_prefix=f"{chunk_id_base}_a{i}",
                sub_question_id=sq.sq_id,
            )
            new_evidence.extend(chunks)

    # Search web (if Tavily key available)
    with Timer() as timer:
        web_results = web_search(query, max_results=search_results_per_query)

    for i, result in enumerate(web_results):
        if result.content:
            chunks = chunk_text(
                text=result.content,
                source_url=result.url,
                source_title=result.title,
                source_type="web",
                chunk_size=config.retrieval.chunk_size,
                chunk_overlap=config.retrieval.chunk_overlap,
                retrieval_round=round_num,
                chunk_id_prefix=f"{chunk_id_base}_w{i}",
                sub_question_id=sq.sq_id,
            )
            new_evidence.extend(chunks)

    # Append to state
    state.evidence.extend(new_evidence)
    sq.rounds_used += 1

    # Record what this round searched for and what came back, so the NEXT
    # round's reformulation can condition on it (and so the whole search
    # trajectory is visible in state.json rather than lost inside the loop).
    source_titles = list(dict.fromkeys(c.source_title for c in new_evidence))
    sq.retrieval_attempts.append(RetrievalAttempt(
        round_num=round_num,
        query=query,
        rationale=reformulation_rationale,
        n_chunks=len(new_evidence),
        source_titles=source_titles[:10],
        gap_noted=gap,
    ))

    log_step(
        state,
        component="researcher",
        step=f"round_{round_num}",
        input_summary=f"SQ: {sq.question[:80]}" + (
            f" | reformulated query: {query[:60]}" if query != sq.question else ""
        ),
        output_summary=f"{len(new_evidence)} chunks ({len(papers)} arXiv, {len(web_results)} web)",
        latency_ms=0,
        cost_tokens=0,
        metadata={
            "round": round_num,
            "new_chunks": len(new_evidence),
            "query": query,
            "reformulated": query != sq.question,
            "gap": gap,
        },
    )

    return new_evidence
