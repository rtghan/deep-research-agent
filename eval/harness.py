"""
Evaluation harness — runs test cases and collects metrics.

Runs the full pipeline on each test case, computes metrics, and saves
results for the ablation comparison and visualization.

Returns a dict keyed by test_case_id for easy lookup by the ablation runner.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.metrics import Metrics, compute_metrics
from eval.test_cases import TEST_CASES
from src.orchestrator.config import Config
from src.orchestrator.pipeline import run_research


def run_eval(
    config: Config | None = None,
    output_prefix: str = "",
    use_mock: bool = False,
    output_base: str = "eval/results",
) -> dict[str, dict]:
    """
    Run the pipeline on all test cases and return results keyed by test_case_id.
    
    Returns: {test_case_id: {"metrics": Metrics, "query": str, "state": ResearchState}}
    """
    if config is None:
        config = Config.load()

    prefix = f"{output_prefix}_" if output_prefix else ""
    out = Path(output_base) / f"{prefix}run"
    out.mkdir(parents=True, exist_ok=True)

    results = {}
    for tc in TEST_CASES:
        tc_id = tc["id"]
        print(f"\n{'='*60}")
        print(f"Running: {tc_id} — {tc['description']}")
        print(f"Query: {tc['query'][:80]}...")
        print(f"{'='*60}")

        tc_output_dir = out / tc_id
        state = run_research(
            tc["query"], config, 
            output_dir=str(tc_output_dir),
            use_mock=use_mock,
        )
        metrics = compute_metrics(
            state, 
            config.verification.support_threshold, 
            config.eval.calibration_bins,
        )

        results[tc_id] = {
            "metrics": metrics,
            "query": tc["query"],
            "stress_test": tc["stress_test"],
            "adaptive_enabled": config.adaptive.enabled,
            "verification_enabled": config.verification.enabled,
        }

        print(f"  Claims: {metrics.total_claims}, Supported: {metrics.supported_claims}")
        print(f"  Support rate: {metrics.claim_support_rate:.2%}")
        print(f"  Calibration error (ECE): {metrics.calibration_error:.4f}")
        print(f"  Contradictions: {metrics.contradiction_count}")
        print(f"  Tokens: {metrics.total_tokens}, Rounds: {metrics.total_rounds}")

    # Save summary (metrics as dicts for JSON)
    summary = {tc_id: {**data, "metrics": data["metrics"].to_dict()} for tc_id, data in results.items()}
    with open(out / "eval_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return results
