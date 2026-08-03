"""
Does model capability fix the judge's position bias?

D031 found the judge (deepseek-chat) agrees with itself only 56.7% of the time
when the same comparison is presented in reverse, is position-locked on 30% of
pairs, and prefers whichever text sits in slot B 69% of the time. That is a
concrete, measured defect in the metric behind the project's headline number.

The obvious question is whether it is a *capability* failure — would a stronger
model simply be more self-consistent? — or a structural property of asking any
LLM to make a pairwise aesthetic judgement. Those imply completely different
fixes: buy a better judge, versus ensemble/redesign the protocol.

Same 30 pairs (same seed, so this is paired across models), same flip-order
protocol: each pair judged twice with A/B deliberately swapped. A reliable judge
returns the same SUBSTANTIVE verdict both ways; a position-biased one returns
the same LETTER both ways.
"""
import sys, os, json, random, glob
from collections import Counter

sys.path.insert(0, ".")
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from src.tools.base import LLMClient
from src.scoring.judge import JUDGE_SYSTEM

N_PAIRS = 30
SEED = 20260802          # identical to D031 -> identical pairs
CANDIDATES = [
    ("deepseek/deepseek-chat", "baseline"),
    ("openai/gpt-4.1", "strong"),
    ("google/gemini-2.5-pro", "strong"),
]

api_key = os.environ["OPENROUTER_API_KEY"]
base_url = "https://openrouter.ai/api/v1"

pairs = []
for path in sorted(glob.glob("eval/results/multiround_evolution_run/*/state.json")):
    st = json.load(open(path))
    ev_by_sq = {}
    for e in st["evidence"]:
        ev_by_sq.setdefault(e.get("sub_question_id"), []).append(e)
    for c in st["claims"]:
        for r in c.get("revisions", []):
            if r["operation"] == "retract" or not r.get("new_text"):
                continue
            pool = ev_by_sq.get(c.get("sub_question_id"), [])[:8]
            if not pool:
                continue
            pairs.append({
                "before": r["prev_text"], "after": r["new_text"], "op": r["operation"],
                "evidence": "\n".join(
                    f"--- Evidence [{i}] (source: {e['source_title']}) ---\n{e['text'][:400]}"
                    for i, e in enumerate(pool)),
            })
random.seed(SEED)
sample = random.sample(pairs, min(N_PAIRS, len(pairs)))
print(f"{len(sample)} pairs, identical across all judges (seed {SEED})\n")

summary = {}
for model, tier in CANDIDATES:
    llm = LLMClient(model=model, api_key=api_key, base_url=base_url,
                    temperature=0.3, max_tokens=800)

    def ask(a, b, ev):
        try:
            res, _ = llm.complete_json(
                system=JUDGE_SYSTEM,
                user=f"Claim A: {a}\n\nClaim B: {b}\n\nEvidence pool:\n{ev}")
        except Exception as exc:
            return None
        v = res.get("better", "equivalent")
        return v if v in ("A", "B", "equivalent") else "equivalent"

    kinds, letters, n = Counter(), Counter(), 0
    for p in sample:
        o1 = ask(p["before"], p["after"], p["evidence"])
        o2 = ask(p["after"], p["before"], p["evidence"])
        if o1 is None or o2 is None:
            continue
        n += 1
        letters[o1] += 1; letters[o2] += 1
        v1 = {"B": "improved", "A": "worse", "equivalent": "same"}[o1]
        v2 = {"A": "improved", "B": "worse", "equivalent": "same"}[o2]
        kinds["consistent" if v1 == v2 else ("position_locked" if o1 == o2 else "inconsistent")] += 1

    decisive = letters["A"] + letters["B"]
    a_share = letters["A"] / decisive if decisive else float("nan")
    summary[model] = {
        "tier": tier, "n": n,
        "consistent": kinds["consistent"] / n if n else 0,
        "position_locked": kinds["position_locked"] / n if n else 0,
        "inconsistent": kinds["inconsistent"] / n if n else 0,
        "a_share": a_share, "equivalent": letters["equivalent"],
    }
    print(f"{model:28s} ({tier:8s}) n={n:2d}  consistent={kinds['consistent']/max(1,n)*100:5.1f}%  "
          f"position_locked={kinds['position_locked']/max(1,n)*100:5.1f}%  "
          f"A-share={a_share*100:5.1f}%  equiv={letters['equivalent']}")

print("\n" + "=" * 78)
print("DOES CAPABILITY FIX THE JUDGE?")
print("=" * 78)
print(f"\n{'model':30s} {'consistent':>11s} {'pos-locked':>11s} {'A-share':>9s}")
for m, s in summary.items():
    print(f"{m:30s} {s['consistent']*100:>10.1f}% {s['position_locked']*100:>10.1f}% "
          f"{s['a_share']*100:>8.1f}%")
print("\n  (50% A-share = unbiased; 0% position-locked = fully order-invariant)")

base = summary.get("deepseek/deepseek-chat", {})
best = max((s for s in summary.values()), key=lambda s: s["consistent"], default=None)
if base and best:
    d = (best["consistent"] - base["consistent"]) * 100
    print(f"\n  best strong model vs baseline consistency: {d:+.1f} pp")
    if d > 15:
        print("  => capability materially helps; buying a better judge is a real fix.")
    elif d > 5:
        print("  => modest improvement; ensembling likely still needed.")
    else:
        print("  => capability does NOT fix it. Pairwise LLM judgement is structurally")
        print("     noisy here, so the fix is protocol (ensemble / randomise / report")
        print("     intervals), not a bigger model.")

json.dump(summary, open("eval/results/judge_tier.json", "w"), indent=2)
print("\nSaved eval/results/judge_tier.json")
