"""
Ablation runner — runs both ablations and generates the wow visualizations.

Ablation 1 (Track A): Adaptive compute vs. Uniform compute
- Runs all test cases with adaptive.enabled=true and adaptive.enabled=false
- Measures: claim-support rate vs. total tokens (cost-quality curve)

Ablation 2 (Track B): Verifier on vs. off
- Runs all test cases with verification.enabled=true and verification.enabled=false
- Measures: calibration error, claim-support rate

Ablation 3 (Claim evolution): evolution off vs. on vs. on-with-self-challenger
- `adaptive`        — evolution off; claims are append-only (the old behaviour)
- `evolution`       — challenger is a DIFFERENT model from the extractor
- `evolution_self`  — challenger is the SAME model that wrote the claims
- Measures: revision rate, reversals, support lift, and — by differencing the
  last two — how much of the challenger's bite comes from model independence.
  Same evidence, same claims, same prompts; only the challenger's identity
  changes, so a drop in challenge_hit_rate for `evolution_self` is a direct
  measurement of self-agreement bias rather than an assertion about it
  (this was the long-standing missing experiment).

Usage: python -m ablations.run_ablation
"""

from __future__ import annotations

import copy
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

    base_config = config if config else Config.load()

    def variant(**overrides) -> Config:
        """
        A fresh deep copy of the base config per mode.

        Every mode must start from the same baseline: the modes previously
        shared one mutated Config object, so each ablation silently inherited
        the previous one's overrides (no_verify also ran with adaptive off).
        With evolution added as a third axis that leakage would make the
        comparisons meaningless.
        """
        cfg = copy.deepcopy(base_config)
        for dotted, value in overrides.items():
            section, _, field = dotted.partition("__")
            setattr(getattr(cfg, section), field, value)
        return cfg

    # --- Ablation 1: Adaptive vs Uniform compute (Track A) ---
    print("=" * 60)
    print("ABLATION 1: Adaptive vs. Uniform Compute (Track A)")
    print("=" * 60)

    # Adaptive mode — evolution off, so this remains the pre-evolution baseline
    # and stays directly comparable to previously reported numbers.
    print("\n--- Running with ADAPTIVE compute (evolution off) ---")
    adaptive_results = run_eval(
        variant(adaptive__enabled=True, evolution__enabled=False),
        output_prefix="adaptive", use_mock=use_mock,
    )
    all_results["adaptive"] = adaptive_results

    print("\n--- Running with UNIFORM compute (evolution off) ---")
    uniform_results = run_eval(
        variant(adaptive__enabled=False, evolution__enabled=False),
        output_prefix="uniform", use_mock=use_mock,
    )
    all_results["uniform"] = uniform_results

    # --- Ablation 2: Verifier on vs. off (Track B) ---
    print("\n" + "=" * 60)
    print("ABLATION 2: Verifier On vs. Off (Track B)")
    print("=" * 60)

    print("\n--- Running with VERIFIER OFF ---")
    # Evolution depends on verification (it re-verifies every revision), so it
    # is off here too — this isolates the verifier, not the two together.
    no_verify_results = run_eval(
        variant(verification__enabled=False, evolution__enabled=False),
        output_prefix="no_verify", use_mock=use_mock,
    )
    all_results["no_verify"] = no_verify_results

    # --- Ablation 3: Claim evolution + self-agreement bias ---
    print("\n" + "=" * 60)
    print("ABLATION 3: Claim Evolution & Self-Agreement Bias")
    print("=" * 60)

    print("\n--- Running with EVOLUTION (independent challenger) ---")
    evolution_results = run_eval(
        variant(adaptive__enabled=True, evolution__enabled=True),
        output_prefix="evolution", use_mock=use_mock,
    )
    all_results["evolution"] = evolution_results

    print("\n--- Running with EVOLUTION (self-challenger — bias probe) ---")
    # Identical to the above except the challenger is the same model that wrote
    # the claims it is attacking.
    self_cfg = variant(adaptive__enabled=True, evolution__enabled=True)
    self_cfg.evolution.challenger_model = self_cfg.llm.sub_step_model
    self_cfg.evolution.challenger_base_url = self_cfg.llm.base_url
    self_cfg.evolution.challenger_api_key_env = self_cfg.llm.api_key_env
    evolution_self_results = run_eval(
        self_cfg, output_prefix="evolution_self", use_mock=use_mock,
    )
    all_results["evolution_self"] = evolution_self_results

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
    modes = ["adaptive", "uniform", "no_verify", "evolution", "evolution_self"]
    print(f"{'Test Case':<25} {'Mode':<16} {'Support%':<10} {'ECE':<8} {'Tokens':<8} {'Rounds':<8}")
    print("-" * 79)
    for mode in modes:
        for tc_id, data in all_results[mode].items():
            m = data["metrics"]
            print(f"{tc_id:<25} {mode:<16} {m.claim_support_rate*100:>7.1f}%  {m.calibration_error:.4f}  {m.total_tokens:>6}  {m.total_rounds:>6}")

    # --- Claim evolution table ---
    print("\n" + "=" * 79)
    print("CLAIM EVOLUTION")
    print("=" * 79)
    print(f"{'Mode':<16} {'Chal':<7} {'Hit%':<8} {'Revised%':<10} {'Narrow':<8} {'Rev':<6} {'Retr':<6} {'SupLift':<9}")
    print("-" * 79)
    for mode in ["evolution", "evolution_self"]:
        rows = all_results[mode].values()
        n = max(1, len(rows))
        agg = lambda f: sum(f(d["metrics"]) for d in rows) / n
        tot = lambda f: sum(f(d["metrics"]) for d in rows)
        print(
            f"{mode:<16} {tot(lambda m: m.challenges_issued):<7} "
            f"{agg(lambda m: m.challenge_hit_rate)*100:>6.1f}%  "
            f"{agg(lambda m: m.revision_rate)*100:>7.1f}%   "
            f"{tot(lambda m: m.narrow_count):<8} "
            f"{tot(lambda m: m.reversal_count):<6} "
            f"{tot(lambda m: m.retraction_count):<6} "
            f"{agg(lambda m: m.support_lift):>+7.3f}"
        )

    # The bias probe: same evidence, same claims — only the challenger differs.
    ind_hit = sum(d["metrics"].challenge_hit_rate for d in all_results["evolution"].values())
    self_hit = sum(d["metrics"].challenge_hit_rate for d in all_results["evolution_self"].values())
    n_cases = max(1, len(all_results["evolution"]))
    print(
        f"\nSelf-agreement bias: independent challenger finds fault in "
        f"{ind_hit/n_cases*100:.1f}% of claims vs. {self_hit/n_cases*100:.1f}% "
        f"for the self-challenger "
        f"(delta {(ind_hit - self_hit)/n_cases*100:+.1f} pp)."
    )


if __name__ == "__main__":
    import sys
    use_mock = "--mock" in sys.argv
    run_ablation(use_mock=use_mock)
