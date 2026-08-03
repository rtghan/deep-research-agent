# Presentation

| File | What it is |
|---|---|
| `slides.html` | **The deck.** Self-contained — no build step, no network. Open in any browser. |

## Presenting

Open `slides.html` directly (`file://` works — nothing is fetched remotely).

| Key | Action |
|---|---|
| `→` `space` | next · click right side of screen |
| `←` | previous · click left side |
| `N` | toggle **presenter notes** (the amber lines) |
| `P` | print → save as PDF |
| `Home` / `End` | first / last slide |

The URL hash tracks the slide (`slides.html#14`), so you can link or resume mid-deck.

## Shape

~24 slides for a 30–40 min slot, budgeted at roughly 34 min spoken to leave air for interruptions.

1. **1–4** — the failure mode, thesis, chosen depth
2. **5–11** — full design (the brief asks for the design, not just what was built)
3. **12** — demo
4. **13–19** — evaluation, the strongest section
5. **20–24** — the unifying pattern, weaknesses, landscape, next steps, close

Slides 7 (claim evolution) and 17–18 (the two results that came back false) are the ones worth
protecting time for. Everything in 5–11 can be compressed if you are running long.

## A note on the numbers

Every figure in the deck traces to a committed artifact under `eval/results/` —
`self_agreement_ablation.json`, `offline_ablations.json`, `judge_position_bias.json`,
`judge_tier.json`, `prompt_injection.json`, `crossover_2x2.json`,
`frozen_pool_convergence.json`, `challenger_memory_convergence.json`,
`strategy_comparison.json`, `capability_vs_architecture.json`.

If a reviewer asks "where does that come from," the answer is a file, not a memory.

## Demo

**Primary: replay a real run.** Deterministic, offline, free, pausable.

```bash
python run.py --replay outputs/real_reform_report/trace.jsonl --speed 2
python run.py --replay outputs/real_reform_report/trace.jsonl --speed 2 --pause-at evolution/evolve
```

That trace is a real `gpt-4o-mini` + `deepseek-chat` run on inference-time compute scaling:
477 steps, 1.05M tokens, 5 sub-questions, 5 reformulated queries, 10 evolution passes
(including one at `2 narrow, 4 reverse, 2 retract`), and a report critique that found
4 defects and revised to v2.

Timing: `--speed 8` ≈ 18s, `--speed 4` ≈ 38s, `--speed 2` ≈ 76s, `--speed 1` ≈ 150s.
Use 2 and pause on the evolution pass.

Replay is not a recording. Narration is a view over the trace, so replay feeds the *same*
renderer the *same* events — reconstructing the whole run from `trace.jsonl` alone is itself
evidence that the trace is a complete record.

**Why not live.** Measured three configurations: a full run is ~17 min; trimmed to 2
sub-questions with 2 challenges per round and the judge off it is ~9.5 min; also dropping
PDF fetch for abstracts it is ~7 min. All well past a 5-minute slot, and that is before
stage variance. The remaining cost is LLM latency, not retrieval — the challenger alone
averages 4.6s per call.

**Live fallback if pushed:** `python run.py --demo --mock` (~2 min, real retrieval, canned
model responses), or start `--demo-live` in a second terminal at the beginning of the demo
section and show its finished output at the end.
