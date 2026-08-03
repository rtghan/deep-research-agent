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

python run.py --demo --mock              # no API key needed
python run.py --demo --narrate           # watch it work (see below)
python run.py "your research question"
python run.py --eval                     # 7-test-case evaluation harness
python run.py --ablation                 # ablations + figures
PYTHONPATH=. python tests/test_evolution.py   # 82 offline checks, ~1s, no network
```

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
| Judge | `src/scoring/judge.py` | Blind pairwise before/after quality |
| Confidence scorer | `src/scoring/confidence.py` | Calibrated per-claim confidence |
| Synthesizer | `src/agents/synthesizer.py` | Report + deterministic confidence index |
| Report critic | `src/agents/report_critic.py` | Does the report answer the question? |
| Report loop | `src/orchestrator/report_loop.py` | Mechanical checks + correction passes |
| Narration | `src/obs/progress.py` | Live process visibility |

Environment: `OPENAI_API_KEY` or `OPENROUTER_API_KEY` (required); `TAVILY_API_KEY` (optional — falls back to DuckDuckGo/Wikipedia).
