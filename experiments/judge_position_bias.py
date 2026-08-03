"""
Judge reliability: position bias and self-consistency.

WHY. "81.7% of revisions judged improvements" is a headline number in the
README, DECISIONS.md, and the presentation. It is produced by a single LLM
doing a pairwise A/B comparison. If that judge has a position preference —
systematically picking whichever text is labelled A, or B — then the 81.7% is
measuring label placement, not quality, and every conclusion resting on it is
void. The judge randomises order per call, which protects the AGGREGATE from a
constant bias but does nothing to tell us whether the judge is reliable at all.

THE TEST. Take real before/after revision pairs from a completed run and put
each one to the judge TWICE, with the order deliberately flipped:
    call 1:  A = before, B = after
    call 2:  A = after,  B = before

A reliable judge gives the same *substantive* verdict both times (both
"improved" or both "worse"). A position-biased judge picks the same *letter*
both times, which flips the substantive verdict. That separation is only
visible if you control the order rather than randomising it.

Three outcomes are distinguishable:
  consistent      — same substantive verdict under both orders  (good)
  position-locked — same LETTER under both orders               (bias)
  inconsistent    — disagrees with itself for neither reason    (noise)
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
SEED = 20260802

api_key = os.environ["OPENROUTER_API_KEY"]
llm = LLMClient(model="deepseek/deepseek-chat", api_key=api_key,
                base_url="https://openrouter.ai/api/v1", temperature=0.3, max_tokens=800)

# Collect real revision pairs, with the evidence pool they were judged against.
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
                "before": r["prev_text"], "after": r["new_text"],
                "op": r["operation"],
                "evidence": "\n".join(
                    f"--- Evidence [{i}] (source: {e['source_title']}) ---\n{e['text'][:400]}"
                    for i, e in enumerate(pool)),
                "original_verdict": r.get("judge_verdict"),
            })

random.seed(SEED)
sample = random.sample(pairs, min(N_PAIRS, len(pairs)))
print(f"{len(pairs)} revision pairs available; testing {len(sample)}\n")


def ask(a_text, b_text, evidence):
    result, _ = llm.complete_json(
        system=JUDGE_SYSTEM,
        user=f"Claim A: {a_text}\n\nClaim B: {b_text}\n\nEvidence pool:\n{evidence}",
    )
    b = result.get("better", "equivalent")
    return b if b in ("A", "B", "equivalent") else "equivalent"


rows = []
for i, p in enumerate(sample, 1):
    # order 1: A=before, B=after   -> "B" means the revision improved it
    o1 = ask(p["before"], p["after"], p["evidence"])
    # order 2: A=after,  B=before  -> "A" means the revision improved it
    o2 = ask(p["after"], p["before"], p["evidence"])

    v1 = {"B": "improved", "A": "worse", "equivalent": "same"}[o1]
    v2 = {"A": "improved", "B": "worse", "equivalent": "same"}[o2]

    if v1 == v2:
        kind = "consistent"
    elif o1 == o2:
        kind = "position_locked"   # same letter both times
    else:
        kind = "inconsistent"

    rows.append({"op": p["op"], "letter1": o1, "letter2": o2,
                 "verdict1": v1, "verdict2": v2, "kind": kind})
    print(f"  {i:2d}/{len(sample)} [{p['op']:8s}] order1={o1} order2={o2} "
          f"-> {v1:8s}/{v2:8s}  {kind}")

n = len(rows)
kinds = Counter(r["kind"] for r in rows)
letters = Counter(r["letter1"] for r in rows) + Counter(r["letter2"] for r in rows)

print("\n" + "=" * 74)
print(f"JUDGE RELIABILITY  (n={n} pairs, {n*2} calls)")
print("=" * 74)
for k in ("consistent", "position_locked", "inconsistent"):
    print(f"  {k:16s} {kinds.get(k,0):3d}  ({kinds.get(k,0)/n*100:5.1f}%)")

print(f"\n  letter chosen overall: A={letters.get('A',0)}  B={letters.get('B',0)}  "
      f"equivalent={letters.get('equivalent',0)}  (of {n*2} calls)")
a_share = letters.get("A", 0) / max(1, (letters.get("A", 0) + letters.get("B", 0)))
print(f"  share of decisive calls choosing 'A': {a_share*100:.1f}%  (50% = unbiased)")

improved = sum(1 for r in rows if r["verdict1"] == "improved")
print(f"\n  'improved' rate in order 1 only: {improved/n*100:.1f}%  "
      f"(headline figure from the full run: 81.7%)")

print(f"\n  agreement with the ORIGINAL judged verdict stored in state.json:")
orig = [(r, s) for r, s in zip(rows, sample) if s.get("original_verdict")]
if orig:
    agree = sum(1 for r, s in orig if r["verdict1"] == s["original_verdict"])
    print(f"    {agree}/{len(orig)} ({agree/len(orig)*100:.1f}%) — "
          f"test-retest at temperature 0.3")

json.dump(rows, open("eval/results/judge_position_bias.json", "w"), indent=2)
print("\nSaved eval/results/judge_position_bias.json")
