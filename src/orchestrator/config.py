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
    synthesis: SynthesisConfig
    eval: EvalConfig

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls(
            llm=LLMConfig(**data.get("llm", {})),
            retrieval=RetrievalConfig(**data.get("retrieval", {})),
            adaptive=AdaptiveConfig(**data.get("adaptive", {})),
            verification=VerificationConfig(**data.get("verification", {})),
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
                synthesis=SynthesisConfig(),
                eval=EvalConfig(),
            )
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)
