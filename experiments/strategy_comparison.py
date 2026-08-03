"""
Three-way effort-allocation comparison: threshold vs scheduler vs uniform.

WHY THIS IS THE ONE THAT MATTERS. The README and the presentation lead with
"98% quality at 3.5x lower cost than uniform" — Track A's headline. That number
was produced by the `threshold` allocator, which D023 later showed can
effectively never grant a third round (0 of 35 sub-questions crossed the bar).
So the headline currently describes a strategy we have since diagnosed as
partly broken, and the replacement (`scheduler`, D027) has only ever run
against a mock.

Three arms, one harness, identical test cases:

  uniform    scheduler with total_round_pool == n_sub_questions.
             Every sub-question gets exactly one round; nothing is adaptive.
             This is the honest baseline, and getting it as a *parameter*
             rather than a separate code path is one of the scheduler's design
             payoffs (D027).
  threshold  the shipped allocator: per-sub-question budget from an absolute
             difficulty bar.
  scheduler  global pool allocated by RANKING marginal value.

Held constant across arms so only allocation differs: models, retrieval
breadth, challenge budget, evidence caps, report correction. The pool is sized
to the threshold arm's expected spend so the comparison is cost-matched rather
than "the one that spent more won."

WHAT WOULD FALSIFY THE SCHEDULER: if it spends the same total rounds as
threshold and gets no better support rate or calibration, then ranking bought
nothing and the extra machinery is unjustified. That is a real possible outcome
and the run is set up to show it.
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

CASES = [tc for tc in TEST_CASES if tc["id"] in
         ("tc2_contradictory", "tc3_sparse", "tc4_factual", "tc7_broad_multiround")]


def base_cfg():
    c = Config.load("configs/openrouter.yaml")
    c.llm.sub_step_model = "openai/gpt-4o-mini"
    c.llm.synthesis_model = "openai/gpt-4o-mini"
    c.llm.max_tokens = 1500
    c.verification.verifier_model = "openai/gpt-4o-mini"
    c.evolution.challenger_model = "deepseek/deepseek-chat"
    c.evolution.max_challenges_per_round = 8
    c.evolution.max_evidence_chunks = 12
    c.retrieval.search_results_per_query = 3
    # Phase 5 off: it can reopen retrieval, which would contaminate a
    # comparison that is specifically about how Phase 2 allocates rounds.
    c.report_correction.enabled = False
    return c


ARMS = {}

cfg = base_cfg()
cfg.adaptive.enabled = True
cfg.adaptive.strategy = "scheduler"
cfg.adaptive.total_round_pool = 5      # == n_sub_questions -> one round each
cfg.adaptive.max_rounds_per_sub_question = 1
ARMS["uniform"] = cfg

cfg = base_cfg()
cfg.adaptive.enabled = True
cfg.adaptive.strategy = "threshold"
cfg.adaptive.min_budget = 1
cfg.adaptive.max_budget = 4
ARMS["threshold"] = cfg

cfg = base_cfg()
cfg.adaptive.enabled = True
cfg.adaptive.strategy = "scheduler"
cfg.adaptive.total_round_pool = 9      # cost-matched to threshold's typical spend
cfg.adaptive.max_rounds_per_sub_question = 4
cfg.adaptive.marginal_value_floor = 0.08
ARMS["scheduler"] = cfg

results = {}
for arm, cfg in ARMS.items():
    print(f"\n{'='*78}\nARM: {arm}  (strategy={cfg.adaptive.strategy}, "
          f"pool={getattr(cfg.adaptive,'total_round_pool','n/a')}, "
          f"max/sq={cfg.adaptive.max_rounds_per_sub_question})\n{'='*78}")
    results[arm] = {}
    for tc in CASES:
        t0 = time.time()
        try:
            st = run_research(tc["query"], cfg,
                              output_dir=f"eval/results/strategy_{arm}/{tc['id']}",
                              use_mock=False)
        except Exception as exc:
            print(f"  {tc['id']}: FAILED {type(exc).__name__}: {exc}")
            continue
        m = compute_metrics(st, cfg.verification.support_threshold, cfg.eval.calibration_bins)
        rounds = {s.sq_id: s.rounds_used for s in st.plan.sub_questions} if st.plan else {}
        results[arm][tc["id"]] = {
            "metrics": m.to_dict(),
            "rounds": rounds,
            "total_rounds": sum(rounds.values()),
            "elapsed_s": round(time.time() - t0, 1),
        }
        print(f"  {tc['id']:22s} claims={m.total_claims:3d} support={m.claim_support_rate*100:5.1f}% "
              f"ECE={m.calibration_error:.3f} tokens={m.total_tokens:>7d} "
              f"rounds={sum(rounds.values()):2d} {list(rounds.values())}")

print("\n" + "=" * 78)
print("THREE-WAY COMPARISON")
print("=" * 78)
print(f"\n{'arm':12s} {'claims':>7s} {'support%':>9s} {'ECE':>7s} {'tokens':>9s} {'rounds':>7s} {'tok/claim':>10s}")
agg = {}
for arm in ARMS:
    rs = results[arm]
    if not rs:
        continue
    n = len(rs)
    claims = sum(r["metrics"]["total_claims"] for r in rs.values())
    sup = sum(r["metrics"]["claim_support_rate"] for r in rs.values()) / n
    ece = sum(r["metrics"]["calibration_error"] for r in rs.values()) / n
    tok = sum(r["metrics"]["total_tokens"] for r in rs.values())
    rnd = sum(r["total_rounds"] for r in rs.values())
    agg[arm] = {"claims": claims, "support": sup, "ece": ece, "tokens": tok, "rounds": rnd}
    print(f"{arm:12s} {claims:>7d} {sup*100:>8.1f}% {ece:>7.3f} {tok:>9d} {rnd:>7d} "
          f"{tok/max(1,claims):>10.0f}")

if "uniform" in agg and "scheduler" in agg and "threshold" in agg:
    u, t, s = agg["uniform"], agg["threshold"], agg["scheduler"]
    print(f"\n  cost multiple vs uniform:  threshold {t['tokens']/max(1,u['tokens']):.2f}x   "
          f"scheduler {s['tokens']/max(1,u['tokens']):.2f}x")
    print(f"  support delta vs uniform:  threshold {(t['support']-u['support'])*100:+.1f}pp   "
          f"scheduler {(s['support']-u['support'])*100:+.1f}pp")
    print(f"  rounds spent:              uniform {u['rounds']}  threshold {t['rounds']}  "
          f"scheduler {s['rounds']}")
    print(f"\n  Did ranking beat thresholding at matched cost?")
    print(f"    tokens  threshold {t['tokens']} vs scheduler {s['tokens']} "
          f"({(s['tokens']/max(1,t['tokens'])-1)*100:+.0f}%)")
    print(f"    support threshold {t['support']*100:.1f}% vs scheduler {s['support']*100:.1f}% "
          f"({(s['support']-t['support'])*100:+.1f}pp)")
    print(f"    ECE     threshold {t['ece']:.3f} vs scheduler {s['ece']:.3f}")

json.dump({"per_case": results, "aggregate": agg},
          open("eval/results/strategy_comparison.json", "w"), indent=2)
print("\nSaved eval/results/strategy_comparison.json")
