"""
Frozen-pool convergence experiment.

THE QUESTION. Every multi-round result so far is confounded: each round also ran
fresh retrieval, so "claims kept changing" could mean either "the loop doesn't
converge" or "new evidence legitimately kept arriving." The earlier evaluation
flagged this explicitly as unresolved. This isolates it by FREEZING the evidence
pool and re-challenging the same claims against the same evidence repeatedly.
If the loop converges, revisions should die out. If it doesn't, claims will keep
churning against evidence that hasn't changed — which would be thrash, not
research.

TWO CONDITIONS, because the obvious single-condition version would be
self-deceiving:

  A. stability_rounds = 2  (production default)
     Claims that survive 2 consecutive challenges get `frozen` and are skipped
     thereafter. This will *look* like convergence almost by construction.

  B. stability_rounds = 999 (freezing effectively disabled)
     Every claim is re-challenged every pass, forever. This measures whether the
     underlying challenge/revise process actually settles on its own.

If A converges but B does not, then `stability_rounds` is a band-aid masking
thrash rather than a convergence mechanism — the loop only appears to settle
because we stopped looking at it. That distinction matters for whether the
scheduler redesign can trust a stopping floor at all.

OSCILLATION is tracked separately from churn: a claim whose text returns to a
value it already held (A -> B -> A) is not making progress, it is cycling. Raw
revision counts cannot distinguish that from steady refinement.
"""
import sys, os, json, copy, hashlib
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
from src.orchestrator.state import Claim, EvidenceChunk, ResearchPlan, ResearchState, SubQuestion
from src.orchestrator.evolution import evolve_claims
from src.tools.base import LLMClient

PASSES = 5
CLAIMS_PER_CASE = 6
CASES = ["tc4_factual", "tc2_contradictory"]   # settled vs contested, deliberately

api_key = os.environ["OPENROUTER_API_KEY"]
base_url = "https://openrouter.ai/api/v1"

def make_cfg(stability_rounds: int) -> Config:
    cfg = Config.load("configs/openrouter.yaml")
    cfg.llm.sub_step_model = "openai/gpt-4o-mini"
    cfg.verification.verifier_model = "openai/gpt-4o-mini"
    cfg.evolution.challenger_model = "deepseek/deepseek-chat"
    cfg.evolution.max_challenges_per_round = 50      # never cap; we want all of them
    cfg.evolution.max_evidence_chunks = 14
    cfg.evolution.stability_rounds = stability_rounds
    cfg.evolution.judge_revisions = False            # measuring convergence, not quality
    return cfg

sub_llm = LLMClient(model="openai/gpt-4o-mini", api_key=api_key, base_url=base_url,
                    temperature=0.3, max_tokens=1500)
challenger_llm = LLMClient(model="deepseek/deepseek-chat", api_key=api_key, base_url=base_url,
                           temperature=0.3, max_tokens=1500)


def build_state():
    """One state holding a sampled subset of claims + their frozen evidence."""
    state = ResearchState(query="frozen pool convergence")
    sqs = []
    for case in CASES:
        raw = json.load(open(f"eval/results/multiround_evolution_run/{case}/state.json"))
        # pick the sub-question with the most active claims
        counts = Counter(c["sub_question_id"] for c in raw["claims"] if c["status"] == "active")
        if not counts:
            continue
        sq_id, _ = counts.most_common(1)[0]
        src_sq = next(s for s in raw["plan"]["sub_questions"] if s["sq_id"] == sq_id)
        new_id = f"{case[:3]}_{sq_id}"

        sq = SubQuestion(sq_id=new_id, question=src_sq["question"])
        sqs.append(sq)

        ev = [e for e in raw["evidence"] if e.get("sub_question_id") == sq_id]
        id_map = {}
        for e in ev:
            new_cid = f"{new_id}__{e['chunk_id']}"
            id_map[e["chunk_id"]] = new_cid
            e = dict(e); e["chunk_id"] = new_cid; e["sub_question_id"] = new_id
            state.evidence.append(EvidenceChunk(**e))

        picked = [c for c in raw["claims"]
                  if c["sub_question_id"] == sq_id and c["status"] == "active"][:CLAIMS_PER_CASE]
        for c in picked:
            c = dict(c)
            c["sub_question_id"] = new_id
            c["claim_id"] = f"{new_id}__{c['claim_id']}"
            c["evidence_ids"] = [id_map[x] for x in c.get("evidence_ids", []) if x in id_map]
            c["revisions"] = []          # reset history: we measure THIS experiment's churn
            c["frozen"] = False
            c["challenges_survived"] = 0
            c["version"] = 1
            state.claims.append(Claim(**c))
    state.plan = ResearchPlan(query=state.query, sub_questions=sqs)
    return state


def h(text: str) -> str:
    return hashlib.md5((text or "").strip().lower().encode()).hexdigest()[:10]


def run_condition(label: str, stability_rounds: int):
    cfg = make_cfg(stability_rounds)
    state = build_state()
    n_claims = len(state.claims)
    history = {c.claim_id: [h(c.text)] for c in state.claims}
    rows = []

    print(f"\n{'='*74}\nCONDITION {label}  (stability_rounds={stability_rounds}, "
          f"{n_claims} claims, evidence FROZEN)\n{'='*74}")

    for p in range(1, PASSES + 1):
        totals = Counter()
        for sq in state.plan.sub_questions:
            s = evolve_claims(state, sq, challenger_llm, sub_llm, sub_llm,
                              cfg, round_num=p, judge_llm=None)
            for k, v in s.items():
                totals[k] += v

        frozen = sum(1 for c in state.claims if c.frozen)
        retracted = sum(1 for c in state.claims if c.status == "retracted")
        # oscillation: did any claim return to a text it already held?
        osc = 0
        for c in state.claims:
            hh = h(c.text)
            prev = history[c.claim_id]
            if hh in prev[:-1]:
                osc += 1
            prev.append(hh)

        challenged = totals["challenged"]
        keep_rate = (totals["keep"] / challenged * 100) if challenged else float("nan")
        rows.append((p, challenged, totals["changed"], keep_rate, frozen, retracted, osc))
        print(f"  pass {p}: challenged={challenged:3d}  changed={totals['changed']:3d}  "
              f"keep={keep_rate:5.1f}%  frozen={frozen:2d}/{n_claims}  "
              f"retracted={retracted:2d}  oscillating={osc}")

    return rows, n_claims


results = {}
for label, sr in (("A_freeze_on", 2), ("B_freeze_off", 999)):
    results[label] = run_condition(label, sr)

print("\n" + "=" * 74)
print("VERDICT")
print("=" * 74)
for label, (rows, n) in results.items():
    changed = [r[2] for r in rows]
    first, last = changed[0], changed[-1]
    trend = "CONVERGING" if last < first * 0.34 else ("settling" if last < first * 0.67 else "NOT converging")
    print(f"\n{label}: revisions per pass = {changed}  -> {trend}")
    print(f"  final frozen {rows[-1][4]}/{n}, total oscillating claims seen: {max(r[6] for r in rows)}")

a_last = results["A_freeze_on"][0][-1][2]
b_last = results["B_freeze_off"][0][-1][2]
print(f"\nfreeze-on final revisions: {a_last}   freeze-off final revisions: {b_last}")
if a_last < b_last:
    print("  => freezing is doing the work; the underlying loop does NOT self-settle.")
    print("     stability_rounds is masking churn rather than reflecting convergence.")
else:
    print("  => the underlying challenge/revise process settles on its own.")

json.dump({k: {"rows": v[0], "n_claims": v[1]} for k, v in results.items()},
          open("eval/results/frozen_pool_convergence.json", "w"), indent=2)
print("\nSaved eval/results/frozen_pool_convergence.json")
