"""
Mock LLM client — returns deterministic responses for testing without API keys.

This enables end-to-end pipeline testing without spending real API tokens.
The mock returns plausible-looking structured JSON for each component,
so the full pipeline (planner → researcher → extractor → verifier → 
confidence → synthesizer) can be exercised in CI or local development.
"""

from __future__ import annotations

import json
import hashlib
from src.tools.base import LLMResponse


class MockLLMClient:
    """Drop-in replacement for LLMClient that returns deterministic mock data."""

    def __init__(self, model: str = "mock", temperature: float = 0.3, max_tokens: int = 2000):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> LLMResponse:
        """Return a mock markdown report for synthesis calls."""
        text = self._generate_report(user)
        return LLMResponse(text=text, input_tokens=200, output_tokens=400, latency_ms=50.0)

    def complete_json(self, system: str, user: str) -> tuple[dict, LLMResponse]:
        """Return mock JSON based on which component is calling."""
        # Detect component from system prompt content
        if "research planner" in system.lower():
            return self._mock_plan(user), LLMResponse(text="{}", input_tokens=100, output_tokens=150, latency_ms=30.0)
        elif "claim extractor" in system.lower():
            return self._mock_claims(user), LLMResponse(text="{}", input_tokens=200, output_tokens=300, latency_ms=40.0)
        elif "evidence verifier" in system.lower():
            return self._mock_verification(user), LLMResponse(text="{}", input_tokens=150, output_tokens=100, latency_ms=35.0)
        elif "contradiction detector" in system.lower():
            return self._mock_contradictions(user), LLMResponse(text="{}", input_tokens=200, output_tokens=100, latency_ms=35.0)
        else:
            return {"result": "mock"}, LLMResponse(text="{}", input_tokens=50, output_tokens=50, latency_ms=20.0)

    def _mock_plan(self, user: str) -> dict:
        return {
            "clarified_query": user.split("Research query:")[-1].strip()[:200] if "Research query:" in user else user[:200],
            "sub_questions": [
                {"question": "What are the core architectural components?"},
                {"question": "What performance tradeoffs exist between approaches?"},
                {"question": "How does this compare to earlier work?"},
            ],
        }

    def _mock_claims(self, user: str) -> dict:
        # Generate 2-3 deterministic claims based on the input
        claims = []
        for i in range(3):
            claims.append({
                "text": f"Mock claim {i}: The approach achieves {70 + i * 10}% improvement on benchmark X.",
                "evidence_indices": [i % 3, (i + 1) % 3],
            })
        return {"claims": claims}

    def _mock_verification(self, user: str) -> dict:
        # Deterministic support score based on claim text hash
        score = 0.6 + (hash(user) % 40) / 100.0
        score = max(0.1, min(0.95, score))
        status = "supported" if score >= 0.5 else "insufficient"
        return {
            "support_score": score,
            "status": status,
            "reasoning": "Mock verification: evidence partially supports the claim.",
        }

    def _mock_contradictions(self, user: str) -> dict:
        # Occasionally find a contradiction
        if "contradict" in user.lower() or len(user) > 500:
            return {"contradictions": [{"claim_a_idx": 0, "claim_b_idx": 1, "description": "Mock: Source A claims improvement while Source B reports no significant change."}]}
        return {"contradictions": []}

    def _generate_report(self, user: str) -> str:
        return """# Research Report

## Executive Summary
Based on the analyzed evidence, several key findings emerge with varying degrees of confidence.

## Findings

### Architectural Components
- [confidence=0.75, status=supported] The architecture uses attention mechanisms as the core component. [Source: arXiv paper]

### Performance Tradeoffs
- [confidence=0.65, status=supported] The approach achieves 80% improvement on benchmark tasks. [Source: arXiv paper]

### Comparison to Earlier Work
- [confidence=0.55, status=insufficient] Evidence suggests improvements over RNN-based approaches. [Source: arXiv paper]

## Contradictions & Disagreements
- No major contradictions detected in the available evidence.

## Known Gaps & Limitations
- Some claims have insufficient evidence and require further investigation.
- Mock mode: this report was generated without real LLM calls.
"""
