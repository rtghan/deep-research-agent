# Presentation

| File | What it is |
|---|---|
| `slides.html` | **The deck.** Self-contained — no build step, no network. Open in any browser. |
| `OUTLINE.md` | The speaking outline the deck was built from: per-slide talking points, anticipated hard questions, and the asset checklist. Keep open on a second screen. |

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
