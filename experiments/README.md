# experiments/

Every experiment behind the numbers in `DECISIONS.md`, `TESTING.md`, and the presentation.
Each script is standalone and writes its results to `eval/results/`, which is committed — so
**every figure quoted anywhere in this repo traces to a file, not to a memory.**

```bash
PYTHONPATH=. python experiments/<script>.py
```

Scripts read credentials from `.env` (see `.env.example`). None contain hardcoded keys.

## Free — replay stored state, no API calls

These were the two most informative experiments in the project, and they cost nothing. After ~$10
of real-model evaluation, the sharpest findings came from re-reading data already on disk.

| Script | Question | Answer | Decision |
|---|---|---|---|
| `offline_ablations.py` | Did claim evolution improve calibration? Do the routing thresholds do anything? | **No** — the entire ECE gain was an arithmetic ceiling (weights summing to 0.8). **Two of four knobs change zero decisions** across their whole range. | D030 |

## Cheap — paired replay, small API cost

| Script | Question | Answer | Decision |
|---|---|---|---|
| `ablation_self_agreement.py` | Does a model go easy on its own claims? | **Refuted** — the self challenger was *harsher*. 0 of 105 claims where only the independent one objected; McNemar p=0.0005. | D026 |
| `crossover_2x2.py` | Same question, confound removed (each model challenges both authorships). | **Inconclusive** — ±20 pp cells; the interaction's two terms have opposite signs. But it confirms the confound: critic harshness 57.9% vs 25.8%. | D033 |
| `frozen_pool_convergence.py` | Does the evolution loop converge when evidence stops changing? | **No.** Flat ~50% keep rate over 5 passes; 6/12 claims oscillate. `stability_rounds` is a circuit breaker, not convergence. | D028 |
| `challenger_memory_ab.py` | Is oscillation a *memory* problem? Give the challenger its own revision history. | **Refuted** — 29 vs 27 revisions (noise), identical oscillation, **0 of 60 stalemates declared**. | D029 |
| `judge_position_bias.py` | Is the judge behind the headline metric reliable? | **No** — 57% self-consistent under order flip, 30% position-locked, 69% prefer slot B. | D031 |
| `judge_tier.py` | Does a stronger model fix that? | **Yes** — gemini-2.5-pro: 90% consistent, **0% position-locked**. | D035 |
| `prompt_injection_test.py` | Can hostile retrieved text flip a verdict? | **1 of 56 attempts (1.8%)**, only via a fake role boundary. | D032 |

## Expensive — full pipeline runs

| Script | Question | Answer | Decision |
|---|---|---|---|
| `strategy_comparison.py` | threshold vs scheduler vs uniform allocation. | **Uniform (1 round each) won on every metric and cost least.** Support saturates at 98–100%, so the metric can't discriminate. | D036 |
| `capability_vs_architecture.py` | Would a better model just replace the machinery? | **2.1× faster, not measurably better, 8.7× cost.** The *strong* challenger produced 3× more ungrounded refutations. | D037 |
| `real_eval_multiround.py` | The 7-case sweep that produced most stored state. | 570 revisions, 715 challenges — the substrate the free experiments replay. | D023 |

## Reading these critically

Three of these scripts print an **automated verdict line, and all three overstated their own data**
(D034). They are left uncorrected on purpose. Each came from a threshold written *before* seeing
results, with no branch for "inconclusive":

- `frozen_pool_convergence.py` — "the process settles on its own" when *neither* arm converged
- `challenger_memory_ab.py` — "partially a memory problem" on a 29-vs-27 difference at n=12
- `crossover_2x2.py` — "SELF-AGREEMENT BIAS PRESENT" on an interaction whose terms had opposite signs

**In every case the table printed above the verdict is correct and the sentence is wrong.**
Trust the tables. A generated conclusion is a claim like any other.
