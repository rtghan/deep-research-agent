"""
Self-agreement bias ablation — PAIRED design.

The long-standing open question:
the entire justification for running the challenger on a DIFFERENT model from
the extractor rests on the assumption that a model asked to critique its own
output tends to ratify it. That has never been measured.

WHY PAIRED, NOT TWO FULL RUNS. `ablations/run_ablation.py` implements this as
two complete pipeline runs (`evolution` vs `evolution_self`). That works, but
the two runs retrieve different evidence and extract different claims, so the
comparison confounds "challenger identity" with "got a different evidence pool
and a different set of claims to attack". With ~50-100 claims per run and an
effect size we expect to be modest, that noise plausibly swamps the signal.

This script instead holds the input FIXED: it replays claims and evidence from
the already-completed 7-test-case multiround run, and challenges each identical
(claim, evidence-pool) pair twice —

    A) independent challenger: deepseek-chat   (differs from the extractor)
    B) self challenger:        gpt-4o-mini     (IS the extractor's model)

— so the only variable is which model is doing the critiquing. Same claim, same
evidence, same prompt, same temperature. That makes it a paired comparison, and
lets us count discordant pairs (McNemar-style): claims where the independent
challenger found fault and the self challenger did not, versus the reverse.

It is also much cheaper than two full pipeline runs: no retrieval, no
extraction, no synthesis — only the challenge calls we actually want to compare.
"""
import sys, os, json, random
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from src.orchestrator.config import Config
from src.orchestrator.state import Claim, EvidenceChunk, ResearchState
from src.orchestrator.evolution import select_challenge_evidence, route_operation
from src.agents.challenger import challenge_claim
from src.tools.base import LLMClient

EXTRACTOR_MODEL = "openai/gpt-4o-mini"      # what actually wrote the claims
INDEPENDENT_CHALLENGER = "deepseek/deepseek-chat"
SELF_CHALLENGER = EXTRACTOR_MODEL            # the bias condition

CLAIMS_PER_TEST_CASE = 15
SEED = 20260802

cfg = Config.load("configs/openrouter.yaml")
cfg.evolution.max_evidence_chunks = 16

api_key = os.environ["OPENROUTER_API_KEY"]
base_url = "https://openrouter.ai/api/v1"

indep_llm = LLMClient(model=INDEPENDENT_CHALLENGER, api_key=api_key,
                      base_url=base_url, temperature=0.3, max_tokens=1500)
self_llm = LLMClient(model=SELF_CHALLENGER, api_key=api_key,
                     base_url=base_url, temperature=0.3, max_tokens=1500)

tcs = ['tc1_multi_source','tc2_contradictory','tc3_sparse','tc4_factual',
       'tc5_factual_2','tc6_active_debate','tc7_broad_multiround']

random.seed(SEED)
rows = []

for tc in tcs:
    path = f'eval/results/multiround_evolution_run/{tc}/state.json'
    raw = json.load(open(path))
    evidence = [EvidenceChunk(**e) for e in raw['evidence']]
    claims = [Claim(**c) for c in raw['claims']]

    # Sample claims deterministically for a reproducible comparison.
    sample = random.sample(claims, min(CLAIMS_PER_TEST_CASE, len(claims)))

    for claim in sample:
        # Replay the claim as the EXTRACTOR originally produced it (pre-revision)
        # -- that is what a challenger actually sees on first contact.
        probe = claim.model_copy(deep=True)
        if probe.original_text:
            probe.text = probe.original_text
        probe.version = 1

        sq_id = probe.sub_question_id
        pool = [e for e in evidence if e.sub_question_id == sq_id]
        if not pool:
            pool = [e for e in evidence if e.chunk_id.startswith(f"{sq_id}_")]
        if not pool:
            continue
        claim_pool = select_challenge_evidence(pool, probe, cfg)

        scratch = ResearchState(query="ablation")
        try:
            a = challenge_claim(scratch, probe, claim_pool, indep_llm, cfg, round_num=0)
            b = challenge_claim(scratch, probe, claim_pool, self_llm, cfg, round_num=0)
        except Exception as e:
            print(f"  skip {tc}/{probe.claim_id}: {type(e).__name__}: {e}")
            continue

        rows.append({
            "test_case": tc,
            "claim_id": probe.claim_id,
            "indep_verdict": a.verdict,
            "self_verdict": b.verdict,
            "indep_found_fault": a.verdict != "sound",
            "self_found_fault": b.verdict != "sound",
            "indep_reasoning": a.reasoning_score,
            "self_reasoning": b.reasoning_score,
            "indep_balance": a.evidence_balance,
            "self_balance": b.evidence_balance,
            "indep_op": route_operation(a, cfg),
            "self_op": route_operation(b, cfg),
            "indep_dropped": a.dropped_ungrounded_refutations,
            "self_dropped": b.dropped_ungrounded_refutations,
            "indep_n_ref_sources": a.n_refuting_sources,
            "self_n_ref_sources": b.n_refuting_sources,
        })
        print(f"  {tc}/{probe.claim_id}: indep={a.verdict}({a.reasoning_score:.2f}) "
              f"self={b.verdict}({b.reasoning_score:.2f})")

out = Path("eval/results/self_agreement_ablation.json")
out.write_text(json.dumps(rows, indent=2))

# ---- Analysis ----
n = len(rows)
print("\n" + "=" * 72)
print(f"SELF-AGREEMENT BIAS — PAIRED ABLATION  (n={n} claims)")
print(f"  independent challenger: {INDEPENDENT_CHALLENGER}")
print(f"  self challenger:        {SELF_CHALLENGER}  (== extractor model)")
print("=" * 72)

if n:
    indep_fault = sum(r["indep_found_fault"] for r in rows)
    self_fault = sum(r["self_found_fault"] for r in rows)
    print(f"\nFound fault (verdict != 'sound'):")
    print(f"  independent: {indep_fault}/{n} ({indep_fault/n*100:.1f}%)")
    print(f"  self:        {self_fault}/{n} ({self_fault/n*100:.1f}%)")
    print(f"  delta:       {(indep_fault-self_fault)/n*100:+.1f} pp")

    # Discordant pairs — the paired-test core.
    both = sum(1 for r in rows if r["indep_found_fault"] and r["self_found_fault"])
    only_indep = sum(1 for r in rows if r["indep_found_fault"] and not r["self_found_fault"])
    only_self = sum(1 for r in rows if not r["indep_found_fault"] and r["self_found_fault"])
    neither = sum(1 for r in rows if not r["indep_found_fault"] and not r["self_found_fault"])
    print(f"\nPaired agreement table:")
    print(f"  both found fault:        {both}")
    print(f"  ONLY independent:        {only_indep}   <- bias evidence if >> only_self")
    print(f"  ONLY self:               {only_self}")
    print(f"  neither:                 {neither}")
    print(f"  raw agreement:           {(both+neither)/n*100:.1f}%")

    # McNemar exact (binomial) on discordant pairs.
    d = only_indep + only_self
    if d:
        from math import comb
        k = min(only_indep, only_self)
        p = sum(comb(d, i) for i in range(k + 1)) / (2 ** d) * 2
        p = min(1.0, p)
        print(f"  McNemar exact p:         {p:.4f}  (discordant n={d})")
    else:
        print(f"  McNemar exact p:         n/a (no discordant pairs)")

    ir = sum(r["indep_reasoning"] for r in rows) / n
    sr = sum(r["self_reasoning"] for r in rows) / n
    print(f"\nMean reasoning_score (higher = judged more warranted):")
    print(f"  independent: {ir:.3f}")
    print(f"  self:        {sr:.3f}   delta {sr-ir:+.3f}")

    print(f"\nMean refuting sources found:")
    print(f"  independent: {sum(r['indep_n_ref_sources'] for r in rows)/n:.2f}")
    print(f"  self:        {sum(r['self_n_ref_sources'] for r in rows)/n:.2f}")

    print(f"\nUngrounded refutations dropped (quote-grounding):")
    print(f"  independent: {sum(r['indep_dropped'] for r in rows)}")
    print(f"  self:        {sum(r['self_dropped'] for r in rows)}")

    print(f"\nVerdict distribution:")
    print(f"  independent: {dict(Counter(r['indep_verdict'] for r in rows))}")
    print(f"  self:        {dict(Counter(r['self_verdict'] for r in rows))}")
    print(f"\nRouted operation distribution:")
    print(f"  independent: {dict(Counter(r['indep_op'] for r in rows))}")
    print(f"  self:        {dict(Counter(r['self_op'] for r in rows))}")

print(f"\nSaved {out}")
