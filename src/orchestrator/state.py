"""
ResearchState — the central state object that flows through the agent pipeline.

Design decision: use pydantic models for all data structures so that state is
explicit, serializable, and type-checked. This makes the JSONL trace trivially
correct (just .model_dump()) and forces every component to declare its I/O.

Why not a dict? A dict hides structure; pydantic surfaces it. The brief penalizes
opaque systems — every component should be able to say what it consumes and produces.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class EvidenceChunk(BaseModel):
    """A retrieved piece of text with provenance — the atomic unit of evidence."""
    chunk_id: str
    source_url: str
    source_title: str
    source_type: str  # "arxiv" | "web"
    text: str
    offset_start: int = 0
    offset_end: int = 0
    retrieval_round: int = 0
    sub_question_id: Optional[str] = None  # which sub-question retrieved this


class ClaimRevision(BaseModel):
    """
    One step in a claim's evolution — what changed, why, and what it did to
    the claim's grounding.

    The revision log is what makes claim evolution auditable: you can replay a
    claim from v1 to vN and see which round's evidence forced each change and
    whether the change actually improved the claim's support.
    """
    version: int                      # the version this revision PRODUCED
    round_num: int                    # retrieval round that triggered it
    operation: str                    # keep|refine|narrow|reverse|retract|split
    prev_text: str
    new_text: str
    rationale: str                    # challenger's critique, condensed
    flaws: list[str] = Field(default_factory=list)
    evidence_balance: float = 0.0     # +1 all-supporting … -1 all-refuting
    support_before: Optional[float] = None
    support_after: Optional[float] = None
    reasoning_before: Optional[float] = None
    reasoning_after: Optional[float] = None
    challenger_model: str = ""

    # --- Independent quality judgement (TESTING.md, 2026-08 evaluation) ---
    # support_after - support_before conflates "got more accurate" with "got
    # harder to fully entail" — a hedged, nuanced claim scores lower on strict
    # entailment even when it is a better reflection of the evidence. The judge
    # is a second, structurally different signal: a blind pairwise comparison
    # of before vs. after text, unaware which one is "the revision".
    judge_verdict: Optional[str] = None   # improved|worse|same|uncertain
    judge_rationale: str = ""


class Claim(BaseModel):
    """An atomic, independently verifiable claim extracted from evidence."""
    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    support_score: Optional[float] = None  # 0.0=contradicted, 1.0=fully supported
    verification_status: Optional[str] = None  # "supported"|"contradicted"|"insufficient"
    confidence: Optional[float] = None  # calibrated confidence
    sub_question_id: Optional[str] = None

    # --- Claim evolution (challenger → reviser loop) ---
    version: int = 1
    status: str = "active"            # active|superseded|retracted
    original_text: Optional[str] = None  # text at v1, for before/after inspection
    reasoning_score: Optional[float] = None  # is the claim WARRANTED by evidence?
    evidence_balance: Optional[float] = None  # source-weighted support vs. refutation
    refuting_evidence_ids: list[str] = Field(default_factory=list)
    flaws: list[str] = Field(default_factory=list)  # from the latest challenge
    revisions: list[ClaimRevision] = Field(default_factory=list)
    challenges_survived: int = 0      # consecutive "keep" verdicts
    frozen: bool = False              # stable — stop paying to re-challenge it

    # --- Oscillation detection (frozen-pool experiment, TESTING.md section 14) ---
    # A claim whose text returns to a value it already held is CYCLING, not
    # being refined: narrow -> reverse -> narrow back. The frozen-pool
    # experiment found 6/12 claims doing exactly this against evidence that
    # never changed, at a steady ~50% keep rate that showed no convergence over
    # 5 passes. Raw revision counts cannot tell that apart from real progress,
    # which is why it needs its own signal.
    #
    # Oscillation is DIAGNOSTIC, not just a bug to suppress: a claim that
    # cannot settle means the evidence genuinely does not determine the answer.
    # That is worth reporting to the reader, not hiding.
    text_history: list[str] = Field(default_factory=list)  # md5 prefixes of past texts
    oscillating: bool = False

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class RetrievalAttempt(BaseModel):
    """
    What one retrieval round actually searched for, and what it got back.

    Kept per-round so a later round can condition its query on what has already
    been tried instead of re-issuing the same search (see
    src/agents/query_reformulator.py). This is also the compaction unit: a
    short digest of prior attempts is far cheaper to feed into a reformulation
    prompt than the accumulated raw evidence pool.
    """
    round_num: int
    query: str
    rationale: str = ""          # why this query (empty for round 1, verbatim sq)
    n_chunks: int = 0
    source_titles: list[str] = Field(default_factory=list)
    gap_noted: str = ""          # what the reformulator said was still missing


class SubQuestion(BaseModel):
    """A node in the research plan DAG."""
    sq_id: str
    question: str
    difficulty: float = 0.5  # Track A: 0.0=easy, 1.0=very hard
    compute_budget: int = 2  # Track A: allocated retrieval rounds
    rounds_used: int = 0
    claim_ids: list[str] = Field(default_factory=list)
    sufficient_evidence: bool = True
    retrieval_attempts: list[RetrievalAttempt] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    """The decomposed query — a list of sub-questions."""
    query: str
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    clarified_query: Optional[str] = None


class ChallengeRecord(BaseModel):
    """
    One adversarial challenge against a claim — logged whether or not it led
    to a revision.

    Keeping the "sound" verdicts (challenges that did NOT land) is what lets us
    measure self-agreement bias: run the same evidence past a challenger that
    shares the extractor's model vs. one that doesn't and compare how often each
    finds anything wrong.
    """
    claim_id: str
    round_num: int
    claim_version: int
    verdict: str                      # sound|needs_nuance|needs_reversal|unsupported
    reasoning_score: float = 0.5
    evidence_balance: float = 0.0
    flaws: list[str] = Field(default_factory=list)
    critique: str = ""
    n_supporting_sources: int = 0
    n_refuting_sources: int = 0
    led_to_revision: bool = False
    challenger_model: str = ""
    # The specific metric/aspect the challenger says is in dispute (e.g.
    # "instruction-following accuracy", not "open-ended generalization").
    # Logged for auditability of the 2026-08 metric-conflation finding
    # (TESTING.md) — a reversal citing evidence about a DIFFERENT dimension
    # than the claim is a red flag visible directly in the trace.
    contested_dimension: str = ""
    # Verbatim quotes the challenger cited as refuting evidence, after
    # dropping any that didn't actually appear in the cited chunk (see
    # challenger.py: quote-grounding rejects "silence implies refutation").
    refuting_quotes: list[str] = Field(default_factory=list)
    dropped_ungrounded_refutations: int = 0


class Contradiction(BaseModel):
    """A detected contradiction between claims from different sources."""
    claim_a_id: str
    claim_b_id: str
    description: str
    source_a: str
    source_b: str


class ReportDefect(BaseModel):
    """
    One problem found in the synthesized report.

    Report-level defects are a different category from claim-level ones. The
    challenger asks "is this claim warranted by evidence"; a report can be built
    entirely from well-warranted claims and still fail the user — by burying the
    answer, asserting a 0.4-confidence claim in confident prose, never
    addressing a sub-question, or quoting a claim that was retracted.
    """
    defect_type: str          # overstatement|unsupported_prose|missing_coverage|
                              # buried_answer|mishandled_contradiction|retracted_claim_cited
    detail: str
    sub_question_id: Optional[str] = None
    severity: str = "medium"  # high|medium|low
    found_by: str = "critic"  # "mechanical" or "critic" — see report_loop.py


class ResearchGap(BaseModel):
    """
    A specific thing the critic says is missing from the evidence base.

    `what_to_find` is deliberately actionable text, not a diagnosis: it is
    written into the target sub-question's retrieval history as a gap note, and
    the query reformulator (src/agents/query_reformulator.py) consumes it to
    build a search query that targets the gap. This is the join between
    report-level self-correction and retrieval-level learning — the critic
    supplies "what we're missing", the reformulator turns it into "what to
    search for instead".
    """
    sub_question_id: str
    what_to_find: str


class ReportCritique(BaseModel):
    """One pass of report-level self-correction."""
    pass_num: int
    report_version: int          # the report version this critique examined
    answers_the_question: bool = True
    verdict: str = "accept"      # accept|revise_report|needs_more_research
    defects: list[ReportDefect] = Field(default_factory=list)
    research_gaps: list[ResearchGap] = Field(default_factory=list)
    revision_instructions: str = ""
    action_taken: str = "none"   # none|revised|reopened_research
    critic_model: str = ""


class TraceEntry(BaseModel):
    """One entry in the structured JSONL execution trace."""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    component: str
    step: str
    input_summary: str
    output_summary: str
    latency_ms: float = 0.0
    cost_tokens: int = 0
    metadata: dict = Field(default_factory=dict)


class ResearchState(BaseModel):
    """The full state of a research run. Every component reads/writes this."""
    query: str
    plan: Optional[ResearchPlan] = None
    evidence: list[EvidenceChunk] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    challenges: list[ChallengeRecord] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    report: Optional[str] = None
    report_version: int = 1
    report_critiques: list[ReportCritique] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)
    total_tokens: int = 0
    total_latency_ms: float = 0.0
