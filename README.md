# Deep Research Agent

Decomposes a research question into sub-questions, retrieves evidence from arXiv and the web, extracts claims, and then — the part that makes it different — **keeps attacking its own claims as new evidence arrives**, revising, reversing, or retracting them, before critiquing the finished report against the question that was actually asked.

Output is a structured report where every claim carries the confidence the system actually computed for it, and claims it withdrew are disclosed rather than quietly dropped.

## The thesis

**Spend more compute where evidence is thin, calibrate confidence honestly, and never let a claim outlive the evidence for it.**

| Track | Idea | Result |
|---|---|---|
| **A — Adaptive test-time compute** | Not all sub-questions are equally hard; allocate retrieval rounds by difficulty | 98% quality at **3.5× lower cost** than uniform allocation |
| **B — Evidence-grounded verification** | Verify every claim against its evidence; detect cross-source contradictions; calibrate confidence | Measured calibration (ECE), reliability diagram, load-bearing verifier |
| **C — Claim evolution** | Claims are not append-only. An adversarial challenger re-attacks every claim against *all* accumulated evidence; a deterministic router decides whether it stands, narrows, reverses, or is retracted | 570 revisions across 7 test cases; **81.7%** judged genuine improvements |

Track C is the core contribution. Tracks A and B are its prerequisites.

## Quickstart

```bash
pip install -e .
cp .env.example .env    # add OPENAI_API_KEY or OPENROUTER_API_KEY

python run.py --demo --mock              # no API key needed
python run.py --demo --narrate           # watch it work (see below)
python run.py "your research question"
python run.py --eval                     # 7-test-case evaluation harness
python run.py --ablation                 # ablations + figures
PYTHONPATH=. python tests/test_evolution.py   # 75 offline checks, ~1s, no network
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

Two design commitments worth calling out, both learned the hard way:

1. **Position changes are arithmetic, not vibes.** Whether contradicting evidence means "add a caveat" or "you were wrong, flip it" is a threshold on a *source-weighted* evidence balance, so the reason a claim reversed is a number in the trace. An aggressive critic cannot flip a well-supported claim by being loud.
2. **When a property must hold, code it — don't ask a model for it.** Refuting evidence must come with a verbatim quote that is mechanically checked against the cited chunk (22% of challenges proposed refutations that failed this). The confidence index is rendered from state, not requested in a prompt — a real model silently dropped that instruction.

### Key components

| Component | File | Role |
|---|---|---|
| Planner | `src/agents/planner.py` | Query → sub-questions |
| Researcher | `src/agents/researcher.py` | arXiv + web retrieval |
| Query reformulator | `src/agents/query_reformulator.py` | Round 2+ searches for what round 1 *missed* |
| Extractor | `src/agents/extractor.py` | Evidence → atomic claims |
| Verifier | `src/scoring/verifier.py` | Does the evidence entail this claim? |
| **Challenger** | `src/agents/challenger.py` | Is this claim *warranted*? (independent model) |
| **Reviser** | `src/agents/reviser.py` | Executes the routed operation |
| **Evolution loop** | `src/orchestrator/evolution.py` | Balance arithmetic + routing |
| Judge | `src/scoring/judge.py` | Blind pairwise before/after quality |
| Confidence scorer | `src/scoring/confidence.py` | Calibrated per-claim confidence |
| Synthesizer | `src/agents/synthesizer.py` | Report + deterministic confidence index |
| **Report critic** | `src/agents/report_critic.py` | Does the report answer the question? |
| **Report loop** | `src/orchestrator/report_loop.py` | Mechanical checks + correction passes |
| Narration | `src/obs/progress.py` | Live process visibility |

## Results

**Track A — adaptive vs. uniform** (4 test cases, real gpt-4o-mini): adaptive **98% support at 31K tokens** vs. uniform **99% at 110K** — same quality, **3.5× cheaper**.

**Track B — verifier ablation**: without the verifier, support rate drops to 0% (no assessment exists) and the difficulty signal loses its Phase-2 input. Load-bearing.

**Track C — claim evolution** (7 test cases, 570 revisions):

| | pre-fix | post-fix |
|---|---|---|
| `reverse` mean Δsupport | −0.316 | **−0.170** |
| `reverse` % improved | 8.2% | **18.9%** |
| Ungrounded refutations dropped | — | **159/715 (22.2%)** |
| Judge verdict | — | **81.7% improved**, 17.9% worse |

**Self-agreement bias — measured, and *not* found.** Paired test (n=105, identical claims and evidence, only the challenger model varying): the *self* challenger was strictly **harsher**, not more lenient — 93.3% vs 81.9% fault-finding, **zero** claims where only the independent challenger objected, McNemar p=0.0005. This contradicts the rationale in D021, which has been corrected in place rather than buried. Confounded by baseline model harshness; the clean version is a 2×2 crossover (see D026).

## Documentation

| Doc | What's in it |
|---|---|
| `ARCHITECTURE.md` | File-by-file walkthrough, data flow, every component's role |
| `DECISIONS.md` | 26 decisions: problem → alternatives rejected → choice → **what it did not solve** |
| `TESTING.md` | Chronological development/testing log, including everything that failed |
| `SUBMISSION_REVIEW.md` | Honest audit of this repo against the assignment brief |

## Configuration

`configs/default.yaml` (OpenAI) and `configs/openrouter.yaml` (OpenRouter). Key knobs:

- `adaptive.enabled` / `min_budget` / `max_budget` — Track A
- `verification.enabled` — Track B ablation switch
- `evolution.challenger_model` — set equal to `llm.sub_step_model` for the self-agreement ablation
- `evolution.min_sources_for_reversal` — how much evidence a position change requires
- `report_correction.max_passes` / `allow_research_reopen` — Phase 5 budget

Environment: `OPENAI_API_KEY` or `OPENROUTER_API_KEY` (required); `TAVILY_API_KEY` (optional — falls back to DuckDuckGo/Wikipedia).

## Honest limitations

1. **No embeddings anywhere.** Chunking is fixed-size character slicing; retrieval is keyword-only; evidence selection for the challenger is round-robin by source, not semantic relevance. For a retrieval-heavy system this is the most conspicuous gap.
2. **Multi-round evolution is rarely reachable.** Across 35 sub-questions, only one ever got 3 rounds. The confidence-based difficulty signal saturates low for any well-documented topic, so `stability_rounds` remains effectively untested.
3. **`refine` is nearly unreachable** — fired once in 570 revisions. Real models don't emit the wording-only flaw labels that trigger it.
4. **Calibration is heuristic, not learned** (ECE ≈ 0.13–0.28). Temperature scaling or isotonic regression on held-out data would be the fix.
5. **Proxy ground truth.** ECE uses `support_score ≥ 0.5` as "correct" — partially circular. The judge and the paired ablation reduce but don't eliminate this.
6. **The quality stack is self-referential.** Verifier, challenger, and judge are all LLMs, and the judge reuses the challenger's client. No independent ground truth anywhere.
7. **Prompt injection is unaddressed.** Arbitrary retrieved web/PDF text goes into verifier and challenger prompts.
8. **No parallelism, retries, caching, or resume.** Sub-questions are independent but run serially; rate limits crashed two runs.
9. **Report critic's mechanical tier is unproven on real data** — it found 0 of 6 defects in its one real run (the failure modes it targets didn't occur), so its justification is still theoretical.
10. **No multi-modal output.** Text only; no diagram or taxonomy generation.
