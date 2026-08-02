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


class Claim(BaseModel):
    """An atomic, independently verifiable claim extracted from evidence."""
    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    support_score: Optional[float] = None  # 0.0=contradicted, 1.0=fully supported
    verification_status: Optional[str] = None  # "supported"|"contradicted"|"insufficient"
    confidence: Optional[float] = None  # calibrated confidence
    sub_question_id: Optional[str] = None


class SubQuestion(BaseModel):
    """A node in the research plan DAG."""
    sq_id: str
    question: str
    difficulty: float = 0.5  # Track A: 0.0=easy, 1.0=very hard
    compute_budget: int = 2  # Track A: allocated retrieval rounds
    rounds_used: int = 0
    claim_ids: list[str] = Field(default_factory=list)
    sufficient_evidence: bool = True


class ResearchPlan(BaseModel):
    """The decomposed query — a list of sub-questions."""
    query: str
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    clarified_query: Optional[str] = None


class Contradiction(BaseModel):
    """A detected contradiction between claims from different sources."""
    claim_a_id: str
    claim_b_id: str
    description: str
    source_a: str
    source_b: str


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
    contradictions: list[Contradiction] = Field(default_factory=list)
    report: Optional[str] = None
    trace: list[TraceEntry] = Field(default_factory=list)
    total_tokens: int = 0
    total_latency_ms: float = 0.0
