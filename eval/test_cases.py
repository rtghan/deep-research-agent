"""
Test cases — four research queries designed to exercise different aspects.

These map to the PDF brief's test cases:
1. Multi-source synthesis (compare architectures across papers)
2. Contradictory sources (chain-of-thought evolution, where researchers disagree)
3. Sparse evidence (retrieval methods comparison)
4. Simpler factual (transformer architecture basics)

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
]
