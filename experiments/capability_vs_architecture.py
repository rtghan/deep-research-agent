"""
Does model capability substitute for the architecture?

THE QUESTION THAT MATTERS. This project's thesis is that claim evolution — an
adversarial challenger, arithmetic routing, revision, re-verification — makes
research output more trustworthy. The obvious skeptical reply is: "wouldn't a
better model just do that anyway?" If a strong model with NO evolution
machinery matches or beats a weak model with all of it, the machinery is
ceremony and the honest conclusion is to delete it and spend the money on
inference instead.

That is a real possible outcome, and it is better to find it now than to be
asked it and not know.

THREE ARMS, identical test cases and identical retrieval settings:

  A  baseline + evolution   gpt-4o-mini sub-steps, deepseek-chat challenger,
                            full evolution loop.  (the shipped system)
  B  strong, NO evolution   gpt-4.1 everywhere, evolution disabled.
                            "just use a better model."
  C  strong + evolution     gpt-4.1 everywhere, full evolution loop.
                            does the machinery still earn its keep at the top?

READING THE RESULT:
  B >= A            -> capability substitutes; the architecture is not earning
                       its cost and should be cut back to a strong single pass.
  A >= B at lower $ -> the architecture buys quality that capability alone does
                       not, which is the thesis.
  C >  B            -> the machinery adds value ON TOP of a strong model, i.e.
                       it is complementary to capability rather than a
                       substitute for it. This is the strongest possible result
                       and also the most surprising one.

Cost and wall-clock are recorded per arm because "should we pay more?" is a
question about the whole trade, not just quality. Arms run SEQUENTIALLY so the
latency numbers mean something.
"""
import sys, os, json, time
from collections import Counter

sys.path.insert(0, ".")
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from src.orchestrator.config import Config
from src.orchestrator.pipeline import run_research
from eval.metrics import compute_metrics
from eval.test_cases import TEST_CASES

WEAK, WEAK_CHALLENGER = "openai/gpt-4o-mini", "deepseek/deepseek-chat"
STRONG = "openai/gpt-4.1"

# $/M tokens, for a blended cost estimate (input-dominated workload).
PRICES = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "deepseek/deepseek-chat": (0.26, 1.03),
    "openai/gpt-4.1": (2.00, 8.00),
}

CASES = [tc for tc in TEST_CASES
         if tc["id"] in ("tc2_contradictory", "tc3_sparse", "tc4_factual")]


def cfg_for(sub, challenger, evolution: bool):
    c = Config.load("configs/openrouter.yaml")
    c.llm.sub_step_model = sub
    c.llm.synthesis_model = sub
    c.llm.max_tokens = 1500
    c.verification.verifier_model = sub
    c.evolution.enabled = evolution
    c.evolution.challenger_model = challenger
    # Held identical across arms so only model/evolution differ.
    c.retrieval.search_results_per_query = 3
    c.adaptive.enabled = True
    c.adaptive.strategy = "threshold"
    c.adaptive.min_budget = 1
    c.adaptive.max_budget = 2
    c.evolution.max_challenges_per_round = 6
    c.evolution.max_evidence_chunks = 10
    c.report_correction.enabled = False   # isolates Phase 2; Phase 5 can reopen retrieval
    return c


ARMS = {
    "A_baseline_evolution": (cfg_for(WEAK, WEAK_CHALLENGER, True), "gpt-4o-mini + deepseek, evolution ON"),
    "B_strong_no_evolution": (cfg_for(STRONG, STRONG, False), "gpt-4.1, evolution OFF"),
    "C_strong_evolution": (cfg_for(STRONG, STRONG, True), "gpt-4.1, evolution ON"),
}

results = {}
for arm, (cfg, label) in ARMS.items():
    print(f"\n{'='*80}\nARM {arm}  —  {label}\n{'='*80}")
    results[arm] = {}
    for tc in CASES:
        t0 = time.time()
        try:
            st = run_research(tc["query"], cfg,
                              output_dir=f"eval/results/capability_{arm}/{tc['id']}",
                              use_mock=False)
        except Exception as exc:
            print(f"  {tc['id']}: FAILED {type(exc).__name__}: {exc}")
            continue
        el = time.time() - t0
        m = compute_metrics(st, cfg.verification.support_threshold, cfg.eval.calibration_bins)

        # crude blended cost: treat 85% of tokens as input (retrieval-heavy)
        pin, pout = PRICES.get(cfg.llm.sub_step_model, (1, 4))
        cost = (m.total_tokens * 0.85 * pin + m.total_tokens * 0.15 * pout) / 1e6

        ungrounded = sum(ch.dropped_ungrounded_refutations for ch in st.challenges)
        results[arm][tc["id"]] = {
            "metrics": m.to_dict(), "elapsed_s": round(el, 1),
            "est_cost_usd": round(cost, 3),
            "challenges": len(st.challenges),
            "ungrounded_dropped": ungrounded,
            "ungrounded_rate": round(ungrounded / max(1, len(st.challenges)), 3),
        }
        print(f"  {tc['id']:22s} claims={m.total_claims:3d} support={m.claim_support_rate*100:5.1f}% "
              f"ECE={m.calibration_error:.3f} tok={m.total_tokens:>7d} "
              f"${cost:5.2f} {el:6.0f}s  ungrounded={ungrounded}/{len(st.challenges)}")

print("\n" + "=" * 80)
print("CAPABILITY vs ARCHITECTURE")
print("=" * 80)
print(f"\n{'arm':24s} {'claims':>7s} {'support':>9s} {'ECE':>7s} {'tokens':>9s} {'cost':>8s} {'wall':>8s}")
agg = {}
for arm in ARMS:
    rs = results[arm]
    if not rs:
        continue
    n = len(rs)
    agg[arm] = {
        "claims": sum(r["metrics"]["total_claims"] for r in rs.values()),
        "support": sum(r["metrics"]["claim_support_rate"] for r in rs.values()) / n,
        "ece": sum(r["metrics"]["calibration_error"] for r in rs.values()) / n,
        "tokens": sum(r["metrics"]["total_tokens"] for r in rs.values()),
        "cost": sum(r["est_cost_usd"] for r in rs.values()),
        "wall": sum(r["elapsed_s"] for r in rs.values()),
        "ungrounded_rate": (sum(r["ungrounded_dropped"] for r in rs.values())
                            / max(1, sum(r["challenges"] for r in rs.values()))),
    }
    a = agg[arm]
    print(f"{arm:24s} {a['claims']:>7d} {a['support']*100:>8.1f}% {a['ece']:>7.3f} "
          f"{a['tokens']:>9d} {a['cost']:>7.2f}$ {a['wall']:>7.0f}s")

if all(k in agg for k in ARMS):
    A, B, C = agg["A_baseline_evolution"], agg["B_strong_no_evolution"], agg["C_strong_evolution"]
    print(f"\n  ungrounded-refutation rate (capability signal, evolution arms only):")
    print(f"    A weak challenger  {A['ungrounded_rate']*100:.1f}%")
    print(f"    C strong challenger {C['ungrounded_rate']*100:.1f}%")

    print(f"\n  B (strong, no evolution) vs A (weak + evolution):")
    print(f"    support {B['support']*100:.1f}% vs {A['support']*100:.1f}%  "
          f"({(B['support']-A['support'])*100:+.1f} pp)")
    print(f"    ECE     {B['ece']:.3f} vs {A['ece']:.3f}")
    print(f"    cost    ${B['cost']:.2f} vs ${A['cost']:.2f}  ({B['cost']/max(0.01,A['cost']):.1f}x)")
    print(f"    wall    {B['wall']:.0f}s vs {A['wall']:.0f}s  ({B['wall']/max(1,A['wall']):.1f}x)")

    print(f"\n  C (strong + evolution) vs B (strong alone) — does the machinery still help?")
    print(f"    support {C['support']*100:.1f}% vs {B['support']*100:.1f}%  "
          f"({(C['support']-B['support'])*100:+.1f} pp)")
    print(f"    ECE     {C['ece']:.3f} vs {B['ece']:.3f}  ({C['ece']-B['ece']:+.3f})")
    print(f"    cost    ${C['cost']:.2f} vs ${B['cost']:.2f}")

    if B["support"] >= A["support"] and B["cost"] > A["cost"] * 3:
        print(f"\n  => capability matches the architecture but costs "
              f"{B['cost']/max(0.01,A['cost']):.0f}x more. Architecture wins on efficiency.")
    elif B["support"] > A["support"]:
        print(f"\n  => CAPABILITY SUBSTITUTES: the strong model alone beats weak+machinery.")
    else:
        print(f"\n  => the architecture beats raw capability on quality at lower cost.")

json.dump({"per_case": results, "aggregate": agg},
          open("eval/results/capability_vs_architecture.json", "w"), indent=2)
print("\nSaved eval/results/capability_vs_architecture.json")
