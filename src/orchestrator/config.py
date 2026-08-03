"""
Configuration loader — reads YAML config and provides typed access.

All tunable parameters live in configs/default.yaml. This keeps the code
clean and makes ablation runs trivial: just override the config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class LLMConfig:
    sub_step_model: str = "gpt-4o-mini"
    synthesis_model: str = "gpt-4o"
    temperature: float = 0.3
    max_tokens: int = 2000
    base_url: str = ""  # OpenRouter or custom endpoint
    api_key_env: str = "OPENAI_API_KEY"  # env var name for API key


@dataclass
class RetrievalConfig:
    search_results_per_query: int = 5
    max_rounds: int = 3
    chunk_size: int = 1500
    chunk_overlap: int = 200
    # Round 2+ retrieval reformulates the search query based on what earlier
    # rounds already found and what is still missing, instead of re-issuing the
    # sub-question verbatim every round. Without this, extra retrieval rounds
    # only page deeper into the same result list — they add volume, not new
    # angles, which is a large part of why accumulating rounds stops moving
    # confidence (TESTING.md section 11 / DECISIONS.md D023).
    reformulate_queries: bool = True


@dataclass
class AdaptiveConfig:
    enabled: bool = True
    min_budget: int = 1
    max_budget: int = 4
    low_confidence_threshold: float = 0.5
    novelty_threshold: float = 0.15


@dataclass
class VerificationConfig:
    enabled: bool = True
    verifier_model: str = "gpt-4o-mini"
    support_threshold: float = 0.4


@dataclass
class EvolutionConfig:
    """
    Claim evolution: the challenger → reviser loop.

    The challenger is deliberately a DIFFERENT model from sub_step_model (which
    does extraction), and can point at a different provider entirely via
    challenger_base_url / challenger_api_key_env. This is the defense against
    self-agreement bias — a model asked to critique its own claims tends to
    ratify them. Setting challenger_model == sub_step_model turns that defense
    off, which is exactly the `evolution_self` ablation.
    """
    enabled: bool = True
    challenger_model: str = "gpt-4o"
    challenger_base_url: str = ""        # "" → inherit llm.base_url
    challenger_api_key_env: str = ""     # "" → inherit llm.api_key_env
    reviser_model: str = ""              # "" → inherit llm.sub_step_model
    max_challenges_per_round: int = 12   # cost cap; lowest-confidence claims first
    stability_rounds: int = 2            # N consecutive "keep" verdicts → frozen
    # A sub-question's pool can run to hundreds of chunks (one arXiv PDF alone
    # chunks into dozens). The challenge prompt is built per claim per round, so
    # the pool must be sampled, not dumped. Because the balance score is
    # SOURCE-weighted, what the challenger needs is breadth of sources — seeing
    # 40 chunks of one paper tells it less than 8 chunks from 8 papers.
    max_evidence_chunks: int = 24        # chunks per challenge prompt
    max_chunks_per_source: int = 3       # round-robin cap, keeps breadth
    # Evidence-balance gates. balance = (w_sup - w_ref) / (w_sup + w_ref),
    # where weights count DISTINCT SOURCES, not chunks.
    nuance_balance_threshold: float = 0.5    # below this → add nuance
    reversal_balance_threshold: float = -0.3  # below this → flip the position
    reasoning_soundness_threshold: float = 0.6  # below this → refine the logic

    # --- Fixes from the 2026-08 real-model evaluation (TESTING.md) ---
    # balance is a RATIO — compute_evidence_balance(0, 1) is -1.0, identical to
    # (0 supporting, 10 refuting). A single dissenting source against a claim
    # with no other coverage should not get the same reversal authority as ten
    # independent sources. Require a minimum total source count before a claim
    # is allowed to reverse or retract on evidence-balance grounds; below it,
    # the claim is downgraded to "narrow" (add a caveat) instead.
    min_sources_for_reversal: int = 2
    # Second, independent quality signal. See ClaimRevision.judge_verdict.
    judge_revisions: bool = True


@dataclass
class ReportCorrectionConfig:
    """
    Report-level self-correction: critique the synthesized report and, if it
    doesn't hold up, revise it or go find more evidence.

    Claim evolution operates per-claim; nothing before this checked whether the
    ASSEMBLED report actually answers the user's question. See
    src/orchestrator/report_loop.py.
    """
    enabled: bool = True
    # The critic runs on a different model from the synthesizer, for the same
    # reason the challenger does (DECISIONS.md D021): a model reviewing its own
    # output ratifies it. Empty = reuse the evolution challenger's client, which
    # is already independent — no third API client needed.
    critic_model: str = ""
    max_passes: int = 2               # hard cap on correction passes
    # Whether the critic may reopen retrieval (expensive) or only re-synthesize
    # (cheap). Re-research is capped to one reopen per sub-question regardless.
    allow_research_reopen: bool = True
    # A sub-question whose active claims average below this is flagged as thin
    # by the mechanical checks before the critic is even called.
    thin_confidence_threshold: float = 0.45
    # Stop early if a pass fails to reduce the high-severity defect count — a
    # critic that keeps finding new complaints is not converging, and "found
    # more fault" is not the same as "is right" (TESTING.md section 11).
    stop_when_not_improving: bool = True


@dataclass
class SynthesisConfig:
    include_gaps: bool = True
    output_format: str = "markdown"


@dataclass
class EvalConfig:
    calibration_bins: int = 10


@dataclass
class Config:
    llm: LLMConfig
    retrieval: RetrievalConfig
    adaptive: AdaptiveConfig
    verification: VerificationConfig
    evolution: EvolutionConfig
    report_correction: ReportCorrectionConfig
    synthesis: SynthesisConfig
    eval: EvalConfig

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls(
            llm=LLMConfig(**data.get("llm", {})),
            retrieval=RetrievalConfig(**data.get("retrieval", {})),
            adaptive=AdaptiveConfig(**data.get("adaptive", {})),
            verification=VerificationConfig(**data.get("verification", {})),
            evolution=EvolutionConfig(**data.get("evolution", {})),
            report_correction=ReportCorrectionConfig(**data.get("report_correction", {})),
            synthesis=SynthesisConfig(**data.get("synthesis", {})),
            eval=EvalConfig(**data.get("eval", {})),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        if path is None:
            path = Path(__file__).parent.parent.parent / "configs" / "default.yaml"
        path = Path(path)
        if not path.exists():
            # Fallback to defaults if config file missing
            return cls(
                llm=LLMConfig(),
                retrieval=RetrievalConfig(),
                adaptive=AdaptiveConfig(),
                verification=VerificationConfig(),
                evolution=EvolutionConfig(),
                report_correction=ReportCorrectionConfig(),
                synthesis=SynthesisConfig(),
                eval=EvalConfig(),
            )
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)
