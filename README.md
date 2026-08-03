# Deep Research Agent

Decomposes a research question into sub-questions, retrieves evidence from arXiv and the web, extracts claims, and keeps attacking its own claims as new evidence arrives, revising, reversing, or retracting them, before critiquing the finished report against the question that was actually asked.

Output is a structured report where every claim carries the confidence the system actually computed for it, and claims it withdrew are disclosed rather than quietly dropped.

Some of the core concepts behind the design:
 - Spend more effort where evidence is thin and calibrate confidence accordingly, never let claims exist without their evidence
 - Not all sub-questions are equally hard; allocate retrieval rounds by difficulty
 - Verify every claim against its evidence; detect cross-source contradictions; calibrate confidence
 - Our knowledge should evolve as we get more evidence, claims should develop as we learn more

## Quickstart

```bash
pip install -e .
cp .env.example .env    # add OPENAI_API_KEY or OPENROUTER_API_KEY

PYTHONPATH=. python tests/test_evolution.py   # 92 offline checks, ~1s, no network, no API key
python run.py --demo --mock              # full pipeline, no API key   (~2 min)
python run.py --demo --narrate           # watch it reason (see below) (~5 min)
python run.py "your research question"
python run.py --demo --parallel          # same, sub-questions concurrently
python run.py --eval --parallel          # 7-case harness, cases in parallel
python run.py --eval                     # 7-case evaluation harness   (~2 h, real API)
python run.py --ablation                 # 5-mode ablation + figures   (hours)
```

**Start with the test suite** — it exercises the routing arithmetic, quote-grounding, evidence sampling, oscillation detection, and the report-critic's mechanical tier in about a second, with no network.

⏱️ **Runtimes are long and output is line-buffered.** `--eval` and `--ablation` run the full pipeline once per test case per mode (35 runs for a full ablation) and hit real arXiv/web retrieval *even with `--mock`*, which only mocks the LLM. Pipe through `python -u` if you want to watch progress; otherwise it will look like it has hung when it hasn't.

## Watching it think (`--narrate`)

Narration is a *view over the trace*, not a parallel logging path — anything that logs is narratable, so the two can't drift. Writes to stderr, so `run.py -n "q" > report.md` still gives a clean report.

```
▸ Researching each sub-question
  [sq_0] What empirical studies exist on inference-time compute scaling?
    ◆ Allocating 2 retrieval round(s) — estimated difficulty 0.38
    · Round 1: searched arXiv + web → 35 evidence chunks
    · Re-examined 8 existing claim(s) against all evidence so far → 6 narrow, 1 reverse, 1 retract
    ◆ Changed position on 2 claim(s) — newer evidence outweighed what they were based on
    · Previous search left a gap: lack of studies on model size vs. inference cost
    ◆ Searching again with a different query — "empirical studies model size inference-time performance metrics"
```

## Architecture

```
Query
 └─ Planner ─────────────────────────────── sub-questions
     └─ per sub-question, per round:
         Difficulty → Allocator (budget)
         Researcher ── round 1: verbatim | round 2+: reformulated to target the gap
         Extractor ── new claims
         Verifier ── support score
         ┌─ CLAIM EVOLUTION ─────────────────────────────────┐
         │ Challenger (independent model, quote-grounded)    │
         │   → evidence balance, reasoning soundness         │
         │ Router (arithmetic, not an LLM judgment)          │
         │   keep | refine | narrow | reverse | retract      │
         │ Reviser → re-verify → blind pairwise judge        │
         └───────────────────────────────────────────────────┘
         Confidence scorer → difficulty feedback → loop?
 ├─ Contradiction detection (cross-source only)
 ├─ Synthesizer → report
 └─ REPORT SELF-CORRECTION (Phase 5)
     mechanical checks (no LLM) → independent critic
     → accept | revise_report | reopen research (feeds the reformulator)
```

### Key components

| Component | File | Role |
|---|---|---|
| Planner | `src/agents/planner.py` | Query → sub-questions |
| Researcher | `src/agents/researcher.py` | arXiv + web retrieval |
| Query reformulator | `src/agents/query_reformulator.py` | Round 2+ searches for what round 1 missed |
| Extractor | `src/agents/extractor.py` | Evidence → atomic claims |
| Verifier | `src/scoring/verifier.py` | Does the evidence entail this claim? |
| Challenger | `src/agents/challenger.py` | Is this claim warranted? (independent model) |
| Reviser | `src/agents/reviser.py` | Executes the routed operation |
| Evolution loop | `src/orchestrator/evolution.py` | Balance arithmetic + routing |
| Scheduler | `src/orchestrator/scheduler.py` | Alternate allocation strategy (ranking vs thresholds) |
| Judge | `src/scoring/judge.py` | Blind pairwise before/after quality |
| Confidence scorer | `src/scoring/confidence.py` | Calibrated per-claim confidence |
| Synthesizer | `src/agents/synthesizer.py` | Report + deterministic confidence index |
| Report critic | `src/agents/report_critic.py` | Does the report answer the question? |
| Report loop | `src/orchestrator/report_loop.py` | Mechanical checks + correction passes |
| Narration | `src/obs/progress.py` | Live process visibility |

Environment: `OPENAI_API_KEY` or `OPENROUTER_API_KEY` (required); `TAVILY_API_KEY` (optional — falls back to DuckDuckGo/Wikipedia).

**Serial vs parallel.** Serial is the default. `--parallel` fans out sub-questions (and test cases under `--eval`); `--serial` forces the reference behaviour back regardless of config; `--workers N` sets the pool size. Sub-questions share no state, so results are identical either way — verified at 9 claims / 9 unique IDs / 20740 tokens in both modes, 1.68× faster. The `scheduler` allocation strategy re-ranks after every round and so always runs serially.

## Where to look

| | |
|---|---|
| `DESIGN.md` | The full production design — architecture, assumptions, and the concerns the brief didn't name |
| `ARCHITECTURE.md` | File-by-file walkthrough of the MVP as-built |
| `DECISIONS.md` | 34 decisions, each ending with what it did not solve |
| `TESTING.md` | Chronological development log, including everything that failed |
| `SUBMISSION_REVIEW.md` | Honest audit of this repo against the brief |
| `experiments/` | Every experiment behind every number, and what each one answered |
| `presentation/` | Slide deck (`slides.html`) + speaking outline |
