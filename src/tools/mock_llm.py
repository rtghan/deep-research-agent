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
        elif "adversarial reviewer" in system.lower():
            return self._mock_challenge(user), LLMResponse(text="{}", input_tokens=400, output_tokens=150, latency_ms=45.0)
        elif "revise research claims" in system.lower():
            return self._mock_revision(user), LLMResponse(text="{}", input_tokens=400, output_tokens=200, latency_ms=45.0)
        elif "comparing two versions" in system.lower():
            return self._mock_judge(user), LLMResponse(text="{}", input_tokens=300, output_tokens=60, latency_ms=30.0)
        elif "directing a literature search" in system.lower():
            return self._mock_reformulation(user), LLMResponse(text="{}", input_tokens=200, output_tokens=60, latency_ms=25.0)
        elif "reviewing a research report" in system.lower():
            return self._mock_report_critique(user), LLMResponse(text="{}", input_tokens=500, output_tokens=150, latency_ms=50.0)
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
        # Deterministic support score based on claim text hash.
        # Uses _stable_hash, not the builtin hash(): hash() is salted per
        # interpreter for str, so mock runs were not actually reproducible
        # across processes — which silently made mock ablation runs
        # incomparable to each other.
        score = 0.6 + (self._stable_hash(user) % 40) / 100.0
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

    @staticmethod
    def _stable_hash(text: str) -> int:
        """
        Deterministic across processes, unlike the builtin hash() for str, which
        is salted per-interpreter by PYTHONHASHSEED.
        """
        return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)

    @staticmethod
    def _count_evidence(user: str) -> int:
        return max(1, user.count("--- Evidence ["))

    @staticmethod
    def _extract_evidence_texts(user: str) -> list[str]:
        """
        Pull each evidence chunk's body text back out of the rendered prompt, so
        the mock challenger can quote REAL text — matching the quote-grounding
        validation in challenger.py, which drops any refuting claim whose quote
        isn't an actual substring of the cited chunk.
        """
        import re
        blocks = re.split(r"--- Evidence \[\d+\][^\n]*---\n", user)[1:]
        return [b.split("\n\n")[0].split("\n---")[0].strip() for b in blocks]

    @staticmethod
    def _extract_claim_text(user: str) -> str:
        for line in user.splitlines():
            if line.startswith("Claim (version"):
                return line.split(":", 1)[-1].strip()
            if line.startswith("Claim:"):
                return line.split(":", 1)[-1].strip()
        return user[:200]

    def _mock_challenge(self, user: str) -> dict:
        """
        Deterministic adversarial verdicts, spread across all four outcomes so
        that every branch of the evolution router is exercised in mock runs —
        including reversal and retraction, which real evidence produces rarely.

        Refuting entries carry a real substring of the cited chunk's text, so
        they survive challenger.py's quote-grounding check the same way a
        genuine model's grounded refutation would.
        """
        claim_text = self._extract_claim_text(user)
        n_ev = self._count_evidence(user)
        texts = self._extract_evidence_texts(user)
        bucket = self._stable_hash(claim_text) % 10
        all_idx = list(range(min(n_ev, 6)))

        # If the prompt carries a revision history, a deterministic subset
        # declares a stalemate -- this is what exercises the challenger-memory
        # branch (D029) in offline/mock runs, where no real model is present to
        # notice it is about to re-litigate a claim.
        if "REVISION HISTORY" in user and bucket >= 5:
            return {
                "reasoning_score": 0.5,
                "flaws": ["cherry_picked"],
                "contested_dimension": "direction of the effect",
                "supporting_evidence_indices": all_idx[:1],
                "refuting_evidence": [],
                "verdict": "needs_nuance",
                "contested_stalemate": True,
                "critique": "Mock: objecting again would undo the previous revision; sources genuinely conflict.",
            }

        def _quote_for(idx: int) -> str:
            if idx < len(texts) and texts[idx]:
                words = texts[idx].split()
                return " ".join(words[: min(8, len(words))]) or texts[idx][:40]
            return "evidence"

        def _refuting(indices: list[int]) -> list[dict]:
            return [{"index": i, "quote": _quote_for(i)} for i in indices]

        if bucket <= 4:  # 50% — claim stands
            return {
                "reasoning_score": 0.75 + (bucket / 100.0),
                "flaws": [],
                "contested_dimension": "",
                "supporting_evidence_indices": all_idx,
                "refuting_evidence": [],
                "verdict": "sound",
                "critique": "Mock challenge: the claim is warranted by the evidence pool.",
            }
        if bucket <= 6:  # 20% — minority dissent → nuance
            return {
                "reasoning_score": 0.55,
                "flaws": ["overgeneralization"],
                "contested_dimension": "scope of applicability",
                "supporting_evidence_indices": all_idx[:-1] or all_idx,
                "refuting_evidence": _refuting(all_idx[-1:]),
                "verdict": "needs_nuance",
                "critique": "Mock challenge: holds on the reported benchmark but is stated without scope.",
            }
        if bucket <= 8:  # 20% — dissent dominates → reversal
            return {
                "reasoning_score": 0.3,
                "flaws": ["cherry_picked", "scope_error"],
                "contested_dimension": "overall effect direction",
                "supporting_evidence_indices": all_idx[:1],
                "refuting_evidence": _refuting(all_idx[1:] or all_idx),
                "verdict": "needs_reversal",
                "critique": "Mock challenge: most sources in the pool report the opposite result.",
            }
        return {  # 10% — unsalvageable
            "reasoning_score": 0.15,
            "flaws": ["unsupported_causality"],
            "contested_dimension": "causal claim",
            "supporting_evidence_indices": [],
            "refuting_evidence": _refuting(all_idx),
            "verdict": "unsupported",
            "critique": "Mock challenge: the evidence does not license this claim at all.",
        }

    def _mock_revision(self, user: str) -> dict:
        """Perform the operation the router asked for, deterministically."""
        operation = "refine"
        for line in user.splitlines():
            if line.startswith("OPERATION TO PERFORM:"):
                operation = line.split(":", 1)[-1].strip()
                break

        claim_text = self._extract_claim_text(user)
        n_ev = self._count_evidence(user)
        indices = list(range(min(n_ev, 3)))

        if operation == "retract":
            return {"operation": "retract", "revised_text": "",
                    "evidence_indices": [], "rationale": "Mock: evidence does not license the claim."}
        if operation == "narrow":
            revised = f"{claim_text.rstrip('.')}, though this holds only on the benchmarks reported and one source finds no significant effect."
            rationale = "Mock: scoped to the reported conditions after minority refuting evidence."
        elif operation == "reverse":
            revised = f"Contrary to the earlier reading of the evidence, the majority of sources find the opposite: {claim_text.rstrip('.').lower()} does not hold once compute is controlled for."
            rationale = "Mock: refuting evidence became dominant, position flipped."
        else:  # refine
            revised = f"{claim_text.rstrip('.')} (measured on the specific benchmark reported, not in general)."
            rationale = "Mock: tightened an overgeneralized inference."

        return {
            "operation": operation,
            "revised_text": revised,
            "evidence_indices": indices,
            "rationale": rationale,
        }

    def _mock_judge(self, user: str) -> dict:
        """
        Deterministic pairwise verdict. Slightly favors whichever label is
        longer, as a stand-in for "the more specific/qualified claim" — not
        meaningful as a quality signal, just deterministic for testing.
        """
        a = user.split("Claim A:")[-1].split("Claim B:")[0].strip()
        b = user.split("Claim B:")[-1].split("Evidence pool:")[0].strip()
        bucket = self._stable_hash(a + b) % 5
        if bucket == 0:
            return {"better": "equivalent", "reasoning": "Mock judge: no meaningful difference."}
        better = "A" if len(a) >= len(b) else "B"
        return {"better": better, "reasoning": "Mock judge: deterministic pairwise pick."}

    def _mock_reformulation(self, user: str) -> dict:
        """
        Deterministic query reformulation. Must return something DIFFERENT from
        the sub-question, or query_reformulator's duplicate-guard rejects it and
        falls back — which would leave the reformulation path untested in mock
        runs.
        """
        sq = ""
        for line in user.splitlines():
            if line.startswith("Sub-question being researched:"):
                sq = line.split(":", 1)[-1].strip()
                break
        round_num = "2"
        for line in user.splitlines():
            if line.startswith("Write the search query for round"):
                round_num = line.rstrip(".").split()[-1]
                break
        base = " ".join(sq.split()[:8]) or "topic"
        return {
            "gap": "Mock: earlier rounds covered the general case but not empirical limitations.",
            "query": f"{base} empirical limitations benchmark round{round_num}",
            "rationale": "Mock: targets the limitation/counter-evidence angle the prior query missed.",
        }

    def _mock_report_critique(self, user: str) -> dict:
        """
        Deterministic report critique. First pass finds defects (so the revise
        path is exercised in mock runs); later passes accept (so the loop
        terminates and the convergence brake isn't the only thing stopping it).
        """
        pass_one = "version 1" in user
        if not pass_one:
            return {
                "answers_the_question": True,
                "verdict": "accept",
                "defects": [],
                "research_gaps": [],
                "revision_instructions": "",
            }

        # Name a real sub-question id if one is visible, so the research-gap
        # path is exercised with an id that actually validates.
        import re as _re
        ids = _re.findall(r"\[(sq_\d+)\]", user)
        gaps = []
        verdict = "revise_report"
        if ids:
            verdict = "needs_more_research"
            gaps = [{"sub_question_id": ids[0],
                     "what_to_find": "Mock: head-to-head empirical comparisons under matched conditions."}]
        return {
            "answers_the_question": False,
            "verdict": verdict,
            "defects": [
                {"defect_type": "buried_answer",
                 "detail": "Mock: the executive summary describes the topic without stating a direct answer.",
                 "sub_question_id": None, "severity": "high"},
                {"defect_type": "overstatement",
                 "detail": "Mock: a low-confidence claim is stated as settled fact.",
                 "sub_question_id": ids[0] if ids else None, "severity": "medium"},
            ],
            "research_gaps": gaps,
            "revision_instructions": "Mock: lead with a direct answer; hedge the low-confidence claims.",
        }

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
