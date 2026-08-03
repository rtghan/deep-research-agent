"""
Two offline ablations. Zero API calls — both replay stored run state.

ABLATION 1 — CONFIDENCE FORMULA (isolates the calibration claim)
We reported ECE roughly halving (0.381 -> 0.184) between the pre-evolution
2-signal formula and the post-evolution 4-signal one. That comparison is
confounded three ways: the formula changed, evolution was added, AND the
challenge budget differed between runs. So it cannot say *why* calibration
improved.

Recomputing confidence offline from the SAME stored claims removes every
confound except the formula itself. The decomposition that matters:

  F1  old 2-signal            (0.5·support + 0.3·diversity)·(1−pen)   max 0.8
  F2  F1 rescaled to sum 1.0  (0.625·support + 0.375·diversity)·(1−pen)
  F3  full 4-signal           .35 sup + .25 reason + .20 div + .20 balance
  F4  4-signal, no reasoning  (weight redistributed)
  F5  4-signal, no balance    (weight redistributed)

  F1 vs F2  = the effect of REMOVING THE 0.8 CEILING alone
  F2 vs F3  = the effect of ADDING the evolution-derived signals alone
  F3 vs F4/F5 = each new signal's individual contribution

ABLATION 2 — ROUTING THRESHOLD SENSITIVITY
`route_operation` is a pure function of a challenge result plus config, and
715 real challenge records are stored across the eval runs. Every hand-tuned
constant in it can therefore be swept for free. The weightings were hand-tuned with no sensitivity analysis; this is that analysis, and it
answers the obvious interview question ("where do your thresholds come from?")
with a curve instead of a shrug.
"""
import sys, json, glob
from collections import Counter

sys.path.insert(0, ".")
import numpy as np
from src.orchestrator.config import Config
from src.agents.challenger import ChallengeResult
from src.orchestrator.evolution import route_operation

RUNS = sorted(glob.glob("eval/results/multiround_evolution_run/*/state.json"))


def ece(confidences, accuracies, bins=10):
    c = np.array(confidences); a = np.array(accuracies)
    if len(c) == 0:
        return float("nan")
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for i in range(bins):
        m = (c >= edges[i]) & (c < edges[i + 1]) if i < bins - 1 else (c >= edges[i]) & (c <= edges[i + 1])
        if m.sum():
            total += (m.sum() / len(c)) * abs(a[m].mean() - c[m].mean())
    return total


def diversity_of(claim, ev_by_id):
    srcs = {ev_by_id[e]["source_title"] for e in claim.get("evidence_ids", []) if e in ev_by_id}
    return min(1.0, 0.25 * len(srcs) + 0.25) if srcs else 0.1


FORMULAS = {
    "F1 old 2-signal (0.8 ceiling)": lambda s, r, d, b, p: (0.50 * s + 0.30 * d) * (1 - p),
    "F2 2-signal rescaled to 1.0":   lambda s, r, d, b, p: (0.625 * s + 0.375 * d) * (1 - p),
    "F3 full 4-signal (shipped)":    lambda s, r, d, b, p: (0.35 * s + 0.25 * r + 0.20 * d + 0.20 * b) * (1 - p),
    "F4 4-signal, no reasoning":     lambda s, r, d, b, p: (0.47 * s + 0.27 * d + 0.26 * b) * (1 - p),
    "F5 4-signal, no balance":       lambda s, r, d, b, p: (0.44 * s + 0.31 * r + 0.25 * d) * (1 - p),
}

print("=" * 78)
print("ABLATION 1 — CONFIDENCE FORMULA (offline recompute, identical claims)")
print("=" * 78)

rows = []
for path in RUNS:
    st = json.load(open(path))
    ev_by_id = {e["chunk_id"]: e for e in st["evidence"]}
    contradicted = set()
    for con in st.get("contradictions", []):
        contradicted.add(con["claim_a_id"]); contradicted.add(con["claim_b_id"])
    for c in st["claims"]:
        if c["status"] != "active" or c.get("support_score") is None:
            continue
        if c.get("reasoning_score") is None:   # never challenged -> can't score F3-F5
            continue
        rows.append({
            "s": c["support_score"],
            "r": c["reasoning_score"],
            "d": diversity_of(c, ev_by_id),
            "b": ((c.get("evidence_balance") or 0.0) + 1.0) / 2.0,
            "p": 0.3 if c["claim_id"] in contradicted else 0.0,
            "correct": 1.0 if c["support_score"] >= 0.5 else 0.0,
        })

print(f"claims with a full signal set: {len(rows)}\n")
print(f"{'formula':32s} {'ECE':>8s} {'mean conf':>10s} {'max conf':>9s} {'range':>14s}")
results = {}
for name, fn in FORMULAS.items():
    conf = [max(0.0, min(1.0, fn(x["s"], x["r"], x["d"], x["b"], x["p"]))) for x in rows]
    acc = [x["correct"] for x in rows]
    e = ece(conf, acc)
    results[name] = e
    print(f"{name:32s} {e:8.4f} {np.mean(conf):10.3f} {max(conf):9.3f} "
          f"{min(conf):.2f}-{max(conf):.2f}")

f1, f2, f3 = results["F1 old 2-signal (0.8 ceiling)"], results["F2 2-signal rescaled to 1.0"], results["F3 full 4-signal (shipped)"]
print(f"\nDECOMPOSITION of the reported improvement:")
print(f"  removing the 0.8 ceiling alone (F1->F2):      ECE {f1:.4f} -> {f2:.4f}   ({f1-f2:+.4f})")
print(f"  adding evolution-derived signals (F2->F3):    ECE {f2:.4f} -> {f3:.4f}   ({f2-f3:+.4f})")
print(f"  total (F1->F3):                              ECE {f1:.4f} -> {f3:.4f}   ({f1-f3:+.4f})")
share = (f1 - f2) / (f1 - f3) * 100 if abs(f1 - f3) > 1e-9 else float("nan")
print(f"  => the ceiling accounts for {share:.0f}% of the total improvement")
print(f"\n  dropping reasoning (F3->F4):  {f3:.4f} -> {results['F4 4-signal, no reasoning']:.4f}")
print(f"  dropping balance   (F3->F5):  {f3:.4f} -> {results['F5 4-signal, no balance']:.4f}")

# ---------------------------------------------------------------- Ablation 2
print("\n" + "=" * 78)
print("ABLATION 2 — ROUTING THRESHOLD SENSITIVITY (715 stored challenges)")
print("=" * 78)

challenges = []
for path in RUNS:
    st = json.load(open(path))
    for ch in st.get("challenges", []):
        challenges.append(ChallengeResult(
            reasoning_score=ch.get("reasoning_score", 0.5),
            flaws=ch.get("flaws", []),
            verdict=ch.get("verdict", "sound"),
            n_supporting_sources=ch.get("n_supporting_sources", 0),
            n_refuting_sources=ch.get("n_refuting_sources", 0),
            evidence_balance=ch.get("evidence_balance", 0.0),
        ))
print(f"replaying {len(challenges)} real challenge records\n")

base = Config.load()

def distribution(cfg):
    return Counter(route_operation(c, cfg) for c in challenges)

def show(label, cfg, baseline=None):
    d = distribution(cfg)
    n = sum(d.values())
    parts = " ".join(f"{op}={d.get(op,0):4d}" for op in ("keep","refine","narrow","reverse","retract"))
    changed = ""
    if baseline is not None:
        diff = sum(abs(d.get(k,0) - baseline.get(k,0)) for k in set(d)|set(baseline)) // 2
        changed = f"   ({diff:3d} decisions differ, {diff/n*100:4.1f}%)"
    print(f"  {label:34s} {parts}{changed}")
    return d

import copy
print("SHIPPED CONFIG:")
base_d = show("(shipped)", base)

print("\nmin_sources_for_reversal — the reversal gate:")
for v in (0, 1, 2, 3, 4):
    cfg = copy.deepcopy(base); cfg.evolution.min_sources_for_reversal = v
    show(f"min_sources={v}" + ("  <- shipped" if v == 2 else ""), cfg, base_d)

print("\nreversal_balance_threshold (how lopsided before flipping):")
for v in (-0.7, -0.5, -0.3, -0.1, 0.0):
    cfg = copy.deepcopy(base); cfg.evolution.reversal_balance_threshold = v
    show(f"reversal_thr={v:+.1f}" + ("  <- shipped" if v == -0.3 else ""), cfg, base_d)

print("\nnuance_balance_threshold (how favourable before a claim stands):")
for v in (0.2, 0.35, 0.5, 0.65, 0.8):
    cfg = copy.deepcopy(base); cfg.evolution.nuance_balance_threshold = v
    show(f"nuance_thr={v:.2f}" + ("  <- shipped" if v == 0.5 else ""), cfg, base_d)

print("\nreasoning_soundness_threshold (when logic alone triggers refine):")
for v in (0.3, 0.45, 0.6, 0.75, 0.9):
    cfg = copy.deepcopy(base); cfg.evolution.reasoning_soundness_threshold = v
    show(f"reasoning_thr={v:.2f}" + ("  <- shipped" if v == 0.6 else ""), cfg, base_d)

json.dump({"confidence_formula_ece": results},
          open("eval/results/offline_ablations.json", "w"), indent=2)
print("\nSaved eval/results/offline_ablations.json")
