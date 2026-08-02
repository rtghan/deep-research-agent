"""
Visualization — generates the two "wow" figures.

1. Adaptive compute cost-quality tradeoff curve (Track A)
   x-axis: total tokens (cost), y-axis: claim-support rate (quality)
   Two lines: uniform vs. adaptive — adaptive should dominate

2. Reliability diagram (Track B)
   x-axis: predicted confidence (binned), y-axis: actual accuracy
   Diagonal = perfect calibration
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from eval.metrics import Metrics


def _get_metric(result, key, default=0):
    """Extract a metric value from either a Metrics object or a dict."""
    m = result.get("metrics") if isinstance(result, dict) else result
    if hasattr(m, key):
        return getattr(m, key)
    elif isinstance(m, dict):
        return m.get(key, default)
    return default


def plot_cost_quality(
    adaptive_results: dict,
    uniform_results: dict,
    output_path: str,
) -> None:
    """Track A wow figure: cost-quality tradeoff curve."""
    fig, ax = plt.subplots(figsize=(8, 6))

    # Extract data using _get_metric (handles both Metrics objects and dicts)
    adj_tokens = [_get_metric(r, "total_tokens") for r in adaptive_results.values()]
    adj_support = [_get_metric(r, "claim_support_rate") for r in adaptive_results.values()]
    uni_tokens = [_get_metric(r, "total_tokens") for r in uniform_results.values()]
    uni_support = [_get_metric(r, "claim_support_rate") for r in uniform_results.values()]

    # Scatter individual test cases
    ax.scatter(uni_tokens, uni_support, c='#e74c3c', s=100, alpha=0.7, label='Uniform compute', marker='s')
    ax.scatter(adj_tokens, adj_support, c='#2ecc71', s=100, alpha=0.9, label='Adaptive compute', marker='o')

    # Mean markers
    if adj_tokens:
        ax.scatter([np.mean(adj_tokens)], [np.mean(adj_support)], c='#27ae60', s=300, marker='D', edgecolors='white', linewidth=2, zorder=5, label=f'Adaptive mean ({np.mean(adj_support)*100:.0f}%)')
    if uni_tokens:
        ax.scatter([np.mean(uni_tokens)], [np.mean(uni_support)], c='#c0392b', s=300, marker='D', edgecolors='white', linewidth=2, zorder=5, label=f'Uniform mean ({np.mean(uni_support)*100:.0f}%)')

    # Connect means with an arrow showing improvement
    if adj_tokens and uni_tokens:
        ax.annotate('', xy=(np.mean(adj_tokens), np.mean(adj_support)),
                     xytext=(np.mean(uni_tokens), np.mean(uni_support)),
                     arrowprops=dict(arrowstyle='->', color='blue', lw=2, ls='--'))
        ax.text((np.mean(adj_tokens) + np.mean(uni_tokens)) / 2,
                 (np.mean(adj_support) + np.mean(uni_support)) / 2 + 0.02,
                 'Adaptive wins', color='blue', fontsize=10, ha='center', fontweight='bold')

    ax.set_xlabel('Total Tokens (Compute Cost)', fontsize=13)
    ax.set_ylabel('Claim Support Rate (Quality)', fontsize=13)
    ax.set_title('Track A: Adaptive vs. Uniform Compute\nCost-Quality Tradeoff', fontsize=14, fontweight='bold')
    ax.legend(fontsize=11, loc='lower right')
    ax.set_ylim(0, 1.05)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(left=0)

    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_reliability_diagram(
    results: dict,
    output_path: str,
    num_bins: int = 10,
) -> None:
    """Track B wow figure: reliability diagram for calibrated confidence."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={'width_ratios': [2, 1]})

    # Collect all claims' confidence and proxy accuracy across all test cases
    # Since we don't have individual claim data in metrics, we use the aggregate
    # avg_confidence and claim_support_rate as a single bin
    confidences = []
    accuracies = []

    for r in results.values():
        avg_conf = _get_metric(r, "avg_confidence", 0.5)
        support_rate = _get_metric(r, "claim_support_rate", 0.5)
        # Each test case contributes one point: (avg_confidence, support_rate)
        confidences.append(avg_conf)
        accuracies.append(support_rate)

    confidences = np.array(confidences)
    accuracies = np.array(accuracies)

    # If we have enough data points, create binned reliability diagram
    if len(confidences) >= num_bins:
        bin_edges = np.linspace(0, 1, num_bins + 1)
        bin_centers = []
        bin_accs = []
        bin_confs = []
        bin_sizes = []

        for i in range(num_bins):
            mask = (confidences >= bin_edges[i]) & (confidences <= bin_edges[i + 1])
            if i < num_bins - 1:
                mask = (confidences >= bin_edges[i]) & (confidences < bin_edges[i + 1])
            bin_size = mask.sum()
            if bin_size > 0:
                bin_centers.append((bin_edges[i] + bin_edges[i + 1]) / 2)
                bin_accs.append(accuracies[mask].mean())
                bin_confs.append(confidences[mask].mean())
                bin_sizes.append(bin_size)

        bin_centers = np.array(bin_centers)
        bin_accs = np.array(bin_accs)
        bin_confs = np.array(bin_confs)
        bin_sizes = np.array(bin_sizes)

        # ECE calculation
        total = len(confidences)
        ece = np.sum((bin_sizes / total) * np.abs(bin_accs - bin_confs))

        # Plot bars
        bar_width = 1.0 / num_bins * 0.8
        ax1.bar(bin_centers, bin_accs, width=bar_width, alpha=0.7, color='#3498db', edgecolor='black', linewidth=0.5, label='Actual accuracy')
        ax1.bar(bin_centers, bin_confs - bin_accs, bottom=bin_accs, width=bar_width, alpha=0.3, color='#e74c3c', edgecolor='black', linewidth=0.5, label='Confidence gap')
    else:
        # Not enough data — plot raw points
        ax1.scatter(confidences, accuracies, c='#3498db', s=100, alpha=0.8, label='Test cases')
        ece = float(np.mean(np.abs(confidences - accuracies)))

    # Perfect calibration line
    ax1.plot([0, 1], [0, 1], 'k--', lw=2, label='Perfect calibration')
    ax1.set_xlabel('Mean Predicted Confidence', fontsize=13)
    ax1.set_ylabel('Actual Accuracy (Claim Support Rate)', fontsize=13)
    ax1.set_title(f'Track B: Reliability Diagram\nECE = {ece:.4f} (lower is better)', fontsize=14, fontweight='bold')
    ax1.legend(fontsize=10, loc='upper left')
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.grid(True, alpha=0.3)

    # Right plot: confidence histogram
    if len(confidences) > 0:
        ax2.hist(confidences, bins=min(10, max(1, len(confidences))), range=(0, 1), color='#2ecc71', alpha=0.7, edgecolor='black')
    ax2.set_xlabel('Confidence', fontsize=13)
    ax2.set_ylabel('Count', fontsize=13)
    ax2.set_title('Confidence Distribution', fontsize=14)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
