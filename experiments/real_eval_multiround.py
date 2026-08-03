"""
Second real-model evaluation: multi-round evolution enabled (adaptive.max_budget
raised so sub-questions can get multiple evolution passes, not just one) across
the enlarged 7-test-case suite, with the reversal fixes (min_sources_for_reversal,
quote-grounding, flaws-triggered refine, independent judge) in place.

Goals this run is meant to answer:
1. Do the reversal fixes actually improve support_lift / reduce bad reversals at
   scale, not just on the single hand-seeded re-check?
2. Does `stability_rounds` freezing actually reduce challenge volume/churn in
   later rounds, or do claims keep getting re-litigated indefinitely?
3. Does the 7-test-case suite (vs. the original 4) change the aggregate
   picture, especially the two new "quiet baseline" (tc5) and "genuine
   contradiction" (tc6) cases?
"""
import sys, os, json, time
sys.path.insert(0, ".")
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from src.orchestrator.config import Config
from eval.harness import run_eval

cfg = Config.load("configs/openrouter.yaml")

cfg.llm.sub_step_model = "openai/gpt-4o-mini"
cfg.llm.synthesis_model = "openai/gpt-4o-mini"
cfg.llm.max_tokens = 1500
cfg.verification.verifier_model = "openai/gpt-4o-mini"
cfg.evolution.challenger_model = "deepseek/deepseek-chat"

# Multi-round: the actual ask. Previous runs capped max_budget at 2, so most
# sub-questions only got ONE evolution pass. Raising to 4 (the project's own
# configured ceiling) lets difficult sub-questions accumulate evidence and get
# challenged repeatedly, which is what stability_rounds is supposed to manage.
cfg.retrieval.search_results_per_query = 3
cfg.adaptive.min_budget = 1
cfg.adaptive.max_budget = 4

# Moderate challenge cap (not the "challenge everyone" cap=30 from the last
# run) -- with 4x the rounds AND 7 (not 4) test cases, cap=30 would be a
# combinatorial cost blowup. 15 was the ballpark the earlier evaluation
# recommended as a practical default.
cfg.evolution.max_challenges_per_round = 15
cfg.evolution.max_evidence_chunks = 16
cfg.evolution.stability_rounds = 2  # unchanged from default; this is what we're testing

print(f"sub_step/verifier/synth = {cfg.llm.sub_step_model}")
print(f"challenger = {cfg.evolution.challenger_model}")
print(f"adaptive budget = {cfg.adaptive.min_budget}-{cfg.adaptive.max_budget}")
print(f"max_challenges_per_round = {cfg.evolution.max_challenges_per_round}\n")

t0 = time.time()
results = run_eval(cfg, output_prefix="multiround_evolution", use_mock=False,
                    output_base="eval/results")
elapsed = time.time() - t0

print(f"\n\nTotal wall time: {elapsed:.0f}s")

summary = {tc_id: data["metrics"].to_dict() for tc_id, data in results.items()}
with open("eval/results/multiround_evolution_metrics.json", "w") as f:
    json.dump(summary, f, indent=2)
print("\nSaved eval/results/multiround_evolution_metrics.json")
