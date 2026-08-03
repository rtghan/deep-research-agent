"""
2x2 crossover — the experiment that settles the self-agreement confound.

THE CONFOUND. An earlier paired test found the "self" challenger (gpt-4o-mini, same model that
wrote the claims) was strictly HARSHER than the independent one (deepseek-chat):
93.3% vs 81.9% fault-finding, McNemar p=0.0005, zero claims where only the
independent challenger objected. That refutes self-agreement bias as a
first-order effect — but it cannot distinguish

    "gpt-4o-mini does not go easy on its own output"        (no bias)
from
    "gpt-4o-mini is simply a harsher critic than deepseek"  (main effect)

because every claim in that test was authored by gpt-4o-mini. Model identity
and authorship were perfectly confounded.

THE FIX. Break the confound by getting claims authored by BOTH models over the
SAME evidence, then crossing:

                        challenged by A        challenged by B
    claims authored A   A/A  (self)            B/A  (independent)
    claims authored B   A/B  (independent)     B/B  (self)

Self-agreement bias is then the INTERACTION, not a main effect:

    bias = [fault(B/A) - fault(A/A)]  +  [fault(A/B) - fault(B/B)]
           ^ how much harsher a foreign      ^ same, mirrored
             critic is on A's claims

If each model goes easy on its own work, both bracketed terms are positive.
Model harshness cancels out because each model appears once on each side.

Step 1 therefore has to CREATE the missing cell: claims authored by
deepseek-chat over the same evidence gpt-4o-mini saw. Without that, no
crossover is possible — which is why that test could only report the confounded
version.
"""
import sys, os, json, glob, random
from collections import Counter
from math import comb

sys.path.insert(0, ".")
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from src.orchestrator.config import Config
from src.orchestrator.state import Claim, EvidenceChunk, ResearchState, SubQuestion
from src.agents.extractor import extract_claims
from src.agents.challenger import challenge_claim
from src.orchestrator.evolution import select_challenge_evidence
from src.tools.base import LLMClient

MODEL_A = "openai/gpt-4o-mini"      # original extractor
MODEL_B = "deepseek/deepseek-chat"  # original challenger
CLAIMS_PER_AUTHOR = 16
SEED = 20260802

api_key = os.environ["OPENROUTER_API_KEY"]
base_url = "https://openrouter.ai/api/v1"
llm_a = LLMClient(model=MODEL_A, api_key=api_key, base_url=base_url, temperature=0.3, max_tokens=1200)
llm_b = LLMClient(model=MODEL_B, api_key=api_key, base_url=base_url, temperature=0.3, max_tokens=1200)

cfg = Config.load("configs/openrouter.yaml")
cfg.evolution.max_evidence_chunks = 12
cfg.evolution.challenger_sees_history = False

# ---- shared evidence: a few real sub-question pools ----
pools = []
for path in sorted(glob.glob("eval/results/multiround_evolution_run/*/state.json"))[:4]:
    st = json.load(open(path))
    by_sq = {}
    for e in st["evidence"]:
        by_sq.setdefault(e.get("sub_question_id"), []).append(e)
    sq_id, ev = max(by_sq.items(), key=lambda kv: len(kv[1]))
    sq_txt = next((s["question"] for s in st["plan"]["sub_questions"] if s["sq_id"] == sq_id),
                  "research question")
    pools.append((sq_txt, ev[:14]))

print(f"{len(pools)} shared evidence pools\n")


def to_chunks(ev, prefix):
    return [EvidenceChunk(
        chunk_id=f"{prefix}_{i}", source_url=e["source_url"], source_title=e["source_title"],
        source_type=e["source_type"], text=e["text"][:900], sub_question_id=prefix,
    ) for i, e in enumerate(ev)]


# ---- STEP 1: extract claims with BOTH authors over the same evidence ----
authored = {"A": [], "B": []}
for pi, (sq_txt, ev) in enumerate(pools):
    for author, llm in (("A", llm_a), ("B", llm_b)):
        prefix = f"p{pi}"
        chunks = to_chunks(ev, prefix)
        state = ResearchState(query=sq_txt)
        state.evidence = chunks
        sq = SubQuestion(sq_id=prefix, question=sq_txt)
        try:
            claims = extract_claims(state, sq, chunks, llm, cfg)
        except Exception as exc:
            print(f"  extraction failed ({author}, pool {pi}): {type(exc).__name__}")
            continue
        for c in claims:
            authored[author].append((c, chunks))
        print(f"  pool {pi} author {author} ({llm.model}): {len(claims)} claims")

random.seed(SEED)
for k in authored:
    random.shuffle(authored[k])
    authored[k] = authored[k][:CLAIMS_PER_AUTHOR]
print(f"\nauthored A: {len(authored['A'])}   authored B: {len(authored['B'])}\n")

# ---- STEP 2: cross every claim against BOTH challengers ----
cells = {}
for author in ("A", "B"):
    for critic, llm in (("A", llm_a), ("B", llm_b)):
        faults, reasons = 0, []
        n = 0
        for claim, chunks in authored[author]:
            probe = claim.model_copy(deep=True)
            pool = select_challenge_evidence(chunks, probe, cfg)
            scratch = ResearchState(query="crossover")
            scratch.evidence = chunks
            try:
                ch = challenge_claim(scratch, probe, pool, llm, cfg)
            except Exception as exc:
                print(f"    skip: {type(exc).__name__}")
                continue
            n += 1
            if ch.verdict != "sound":
                faults += 1
            reasons.append(ch.reasoning_score)
        rate = faults / n if n else float("nan")
        cells[(author, critic)] = {"n": n, "faults": faults, "fault_rate": rate,
                                   "mean_reasoning": sum(reasons)/len(reasons) if reasons else 0}
        rel = "SELF" if author == critic else "independent"
        print(f"  claims by {author}, critiqued by {critic} ({rel:11s}): "
              f"fault {faults}/{n} = {rate*100:5.1f}%   mean reasoning {cells[(author,critic)]['mean_reasoning']:.3f}")

# ---- analysis ----
print("\n" + "=" * 78)
print("2x2 CROSSOVER — self-agreement bias as an INTERACTION")
print("=" * 78)
print(f"\n  A = {MODEL_A}   B = {MODEL_B}\n")
print(f"  {'':22s} {'critic A':>12s} {'critic B':>12s}")
for author in ("A", "B"):
    ra = cells[(author, "A")]["fault_rate"] * 100
    rb = cells[(author, "B")]["fault_rate"] * 100
    print(f"  claims authored {author:1s}      {ra:11.1f}% {rb:11.1f}%"
          f"    {'(A=self)' if author=='A' else '(B=self)'}")

aa = cells[("A", "A")]["fault_rate"]; ba = cells[("A", "B")]["fault_rate"]
bb = cells[("B", "B")]["fault_rate"]; ab = cells[("B", "A")]["fault_rate"]

term_a = ba - aa   # foreign critic minus self critic, on A's claims
term_b = ab - bb   # foreign critic minus self critic, on B's claims
bias = term_a + term_b

print(f"\n  leniency toward own work:")
print(f"    A's claims: foreign critic {ba*100:.1f}% - self {aa*100:.1f}% = {term_a*100:+.1f} pp")
print(f"    B's claims: foreign critic {ab*100:.1f}% - self {bb*100:.1f}% = {term_b*100:+.1f} pp")
print(f"    INTERACTION (bias estimate)                    = {bias*100:+.1f} pp")
print(f"\n  main effect of critic harshness (cancels in the interaction):")
print(f"    critic A overall {(aa+ab)/2*100:.1f}%   critic B overall {(ba+bb)/2*100:.1f}%")

if bias > 0.10:
    verdict = "SELF-AGREEMENT BIAS PRESENT: both models go easier on their own claims."
elif bias < -0.10:
    verdict = "REVERSE effect: both models are HARSHER on their own claims."
else:
    verdict = "NO material self-agreement bias once critic harshness is controlled for."
print(f"\n  => {verdict}")
print(f"     (the confounded estimate came from the 'claims authored A' row alone.)")

json.dump({str(k): v for k, v in cells.items()} | {"interaction_pp": bias * 100},
          open("eval/results/crossover_2x2.json", "w"), indent=2)
print("\nSaved eval/results/crossover_2x2.json")
