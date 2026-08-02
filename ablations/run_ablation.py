"""
Ablation runner — runs both ablations and generates the wow visualizations.

Ablation 1 (Track A): Adaptive compute vs. Uniform compute
- Runs all test cases with adaptive.enabled=true and adaptive.enabled=false
- Measures: claim-support rate vs. total tokens (cost-quality curve)

Ablation 2 (Track B): Verifier on vs. off
- Runs all test cases with verification.enabled=true and verification.enabled=false
- Measures: calibration error, claim-support rate

Usage: python -m ablations.run_ablation
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.harness import run_eval
from eval.metrics import compute_metrics
from eval.visualize import plot_cost_quality, plot_reliability_diagram
from src.orchestrator.config import Config


def run_ablation(use_mock: bool = False, config: 'Config | None' = None) -> None:
    """Run both ablations and generate visualizations."""
    results_dir = Path("ablations/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}

    from src.orchestrator.config import Config as ConfigClass

    # --- Ablation 1: Adaptive vs Uniform compute (Track A) ---
    print("=" * 60)
    print("ABLATION 1: Adaptive vs. Uniform Compute (Track A)")
    print("=" * 60)

    # Adaptive mode
    config_adaptive = config if config else Config.load()
    config_adaptive.adaptive.enabled = True
    print("\n--- Running with ADAPTIVE compute ---")
    adaptive_results = run_eval(config_adaptive, output_prefix="adaptive", use_mock=use_mock)
    all_results["adaptive"] = adaptive_results

    # Uniform mode
    config_uniform = Config.load() if not config else config_adaptive
    config_uniform.adaptive.enabled = False
    print("\n--- Running with UNIFORM compute ---")
    uniform_results = run_eval(config_uniform, output_prefix="uniform", use_mock=use_mock)
    all_results["uniform"] = uniform_results

    # --- Ablation 2: Verifier on vs. off (Track B) ---
    print("\n" + "=" * 60)
    print("ABLATION 2: Verifier On vs. Off (Track B)")
    print("=" * 60)

    config_no_verify = Config.load() if not config else config_adaptive
    config_no_verify.verification.enabled = False
    print("\n--- Running with VERIFIER OFF ---")
    no_verify_results = run_eval(config_no_verify, output_prefix="no_verify", use_mock=use_mock)
    all_results["no_verify"] = no_verify_results

    # --- Generate visualizations ---
    print("\n" + "=" * 60)
    print("Generating visualizations...")
    print("=" * 60)

    # Wow moment 1: Cost-quality curve (Track A)
    plot_cost_quality(
        adaptive_results=adaptive_results,
        uniform_results=uniform_results,
        output_path=str(results_dir / "cost_quality_curve.png"),
    )
    print(f"  ✓ Cost-quality curve: {results_dir / 'cost_quality_curve.png'}")

    # Wow moment 2: Reliability diagram (Track B)
    # Use adaptive results (with verifier on) for the reliability diagram
    plot_reliability_diagram(
        results=adaptive_results,
        output_path=str(results_dir / "reliability_diagram.png"),
    )
    print(f"  ✓ Reliability diagram: {results_dir / 'reliability_diagram.png'}")

    # Save summary
    summary = {}
    for mode, results in all_results.items():
        summary[mode] = {}
        for tc_id, data in results.items():
            summary[mode][tc_id] = data["metrics"].to_dict()

    with open(results_dir / "ablation_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  ✓ Summary: {results_dir / 'ablation_summary.json'}")

    # Print comparison table
    print("\n" + "=" * 60)
    print("ABLATION RESULTS SUMMARY")
    print("=" * 60)
    print(f"{'Test Case':<25} {'Mode':<12} {'Support%':<10} {'ECE':<8} {'Tokens':<8} {'Rounds':<8}")
    print("-" * 71)
    for mode in ["adaptive", "uniform", "no_verify"]:
        for tc_id, data in all_results[mode].items():
            m = data["metrics"]
            print(f"{tc_id:<25} {mode:<12} {m.claim_support_rate*100:>7.1f}%  {m.calibration_error:.4f}  {m.total_tokens:>6}  {m.total_rounds:>6}")


if __name__ == "__main__":
    import sys
    use_mock = "--mock" in sys.argv
    run_ablation(use_mock=use_mock)
