"""
Evaluation harness — runs test cases and collects metrics.

Runs the full pipeline on each test case, computes metrics, and saves
results for the ablation comparison and visualization.

Returns a dict keyed by test_case_id for easy lookup by the ablation runner.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    def run_one(tc: dict) -> tuple[str, dict]:
        """One complete test case. Test cases share no state whatsoever."""
        tc_id = tc["id"]
        state = run_research(
            tc["query"], config,
            output_dir=str(out / tc_id),
            use_mock=use_mock,
        )
        metrics = compute_metrics(
            state,
            config.verification.support_threshold,
            config.eval.calibration_bins,
        )
        return tc_id, {
            "metrics": metrics,
            "query": tc["query"],
            "stress_test": tc["stress_test"],
            "adaptive_enabled": config.adaptive.enabled,
            "verification_enabled": config.verification.enabled,
        }

    def report(tc_id: str, data: dict) -> None:
        m = data["metrics"]
        print(f"\n{'='*60}\n{tc_id}\n{'='*60}")
        print(f"  Claims: {m.total_claims}, Supported: {m.supported_claims}")
        print(f"  Support rate: {m.claim_support_rate:.2%}")
        print(f"  Calibration error (ECE): {m.calibration_error:.4f}")
        print(f"  Contradictions: {m.contradiction_count}")
        print(f"  Tokens: {m.total_tokens}, Rounds: {m.total_rounds}")

    results = {}
    # Test cases are entirely independent runs — separate ResearchState,
    # separate output directory, nothing shared. This is the safest
    # parallelism available here, and the eval sweeps are where the wall-clock
    # actually hurt (~2.3 h for 7 cases, ~12 h for an earlier sweep).
    if getattr(config, "execution", None) and config.execution.parallel_test_cases:
        workers = min(config.execution.max_case_workers, len(TEST_CASES))
        print(f"Running {len(TEST_CASES)} test cases with {workers} workers\n")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_one, tc): tc["id"] for tc in TEST_CASES}
            for fut in as_completed(futures):
                tc_id = futures[fut]
                try:
                    tc_id, data = fut.result()
                except Exception as exc:
                    print(f"  {tc_id}: FAILED {type(exc).__name__}: {exc}")
                    continue
                results[tc_id] = data
                report(tc_id, data)
    else:
        for tc in TEST_CASES:
            print(f"\n{'='*60}")
            print(f"Running: {tc['id']} — {tc['description']}")
            print(f"Query: {tc['query'][:80]}...")
            print(f"{'='*60}")
            tc_id, data = run_one(tc)
            results[tc_id] = data
            report(tc_id, data)

    # Save summary (metrics as dicts for JSON)
    summary = {tc_id: {**data, "metrics": data["metrics"].to_dict()} for tc_id, data in results.items()}
    with open(out / "eval_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return results
