"""
Test cases — research queries designed to exercise different aspects.

Test cases 1-4 map to the PDF brief's original test cases:
1. Multi-source synthesis (compare architectures across papers)
2. Contradictory sources (chain-of-thought evolution, where researchers disagree)
3. Sparse evidence (retrieval methods comparison)
4. Simpler factual (transformer architecture basics)

Test cases 5-7 were added to enlarge the suite past 4 questions (4 being too
small for statistical significance) and to specifically
stress claim evolution across multiple rounds and a second data point in the
"quiet baseline" bucket, rather than relying on tc4 alone:
5. A second factual baseline, different domain, for more signal on the
   "evolution should be quiet on settled facts" behavior.
6. A genuinely contested, actively-debated claim (are emergent LLM abilities
   real or a measurement artifact?) — designed to produce real reversals, not
   just nuance, since the two sides of this debate directly contradict rather
   than merely qualify each other.
7. A deliberately broad comparative query (5 competing approaches, not 2-3)
   meant to decompose into more sub-questions and need more retrieval rounds
   per sub-question — a stress test for multi-round evolution convergence
   (does churn settle via `stability_rounds`, or keep getting re-litigated?).

Each test case is annotated with what aspect it stress-tests.
"""

TEST_CASES = [
    {
        "id": "tc1_multi_source",
        "query": "Compare the architectural choices in three frontier LLM papers (GPT-4, Llama, Mistral) and what tradeoffs each makes.",
        "description": "Multi-source synthesis: requires finding and comparing papers from different groups.",
        "stress_test": "synthesis",
    },
    {
        "id": "tc2_contradictory",
        "query": "Trace the evolution of chain-of-thought prompting and identify where researchers disagree about its effectiveness.",
        "description": "Contradictory sources: CoT literature has active disagreement (works vs. doesn't work, why).",
        "stress_test": "contradiction_detection",
    },
    {
        "id": "tc3_sparse",
        "query": "Compare sparse retrieval (BM25) vs. dense retrieval methods and when each is superior.",
        "description": "Sparse/conflicting evidence: retrieval literature has nuanced tradeoffs.",
        "stress_test": "nuanced_reasoning",
    },
    {
        "id": "tc4_factual",
        "query": "What are the key architectural innovations in the Transformer paper 'Attention Is All You Need'?",
        "description": "Simpler factual: single well-known source, should be high-confidence.",
        "stress_test": "baseline_confidence",
    },
    {
        "id": "tc5_factual_2",
        "query": "How does the LoRA (Low-Rank Adaptation) method work for fine-tuning large language models?",
        "description": "Second factual baseline (different domain from tc4): single well-known technique, should also be high-confidence and low-churn.",
        "stress_test": "baseline_confidence",
    },
    {
        "id": "tc6_active_debate",
        "query": "Are emergent abilities in large language models a real phenomenon or an artifact of how they are measured?",
        "description": "Genuinely contradictory claims, not just qualifications — the 'emergent abilities are a mirage' line of work directly disputes the 'emergent abilities are real' line, unlike CoT (tc2) where both sides mostly agree it helps sometimes and disagree on when.",
        "stress_test": "contradiction_detection",
    },
    {
        "id": "tc7_broad_multiround",
        "query": "Compare five approaches to long-context handling in transformers — sparse attention, sliding window attention, retrieval augmentation, state-space models, and position interpolation — and evaluate the tradeoffs of each.",
        "description": "Deliberately broad (5 approaches, not 2-3): more sub-questions, more evidence per sub-question, and higher initial difficulty should push sub-questions toward the top of the adaptive budget range, giving claim evolution multiple rounds to converge (or fail to).",
        "stress_test": "multi_round_convergence",
    },
]
