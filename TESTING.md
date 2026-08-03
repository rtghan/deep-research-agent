# TESTING.md — Claim Evolution: Development & Testing Log

> How the challenger → reviser claim-evolution feature (DECISIONS.md D020–D022) was built, tested, and hardened — in the order it actually happened, including the failures. This is a testing/process record, not a spec; see ARCHITECTURE.md §9.5–9.6 for how the mechanism works and DECISIONS.md D020–D022 for why it's built the way it is.

---

## 1. What was being tested

The original pipeline was append-only: `extract_claims` mints new claims from each round's new evidence and never revisits a claim from an earlier round. The feature under test — challenger + reviser, orchestrated by `src/orchestrator/evolution.py` — makes claims first-class evolving objects: every active claim is re-challenged against the *full* accumulated evidence pool after each round, and a deterministic router (arithmetic on source-weighted evidence balance, not an LLM's say-so) decides whether it stands, gets a caveat, gets reversed, or gets retracted.

Testing needed to answer three questions the design alone couldn't:
1. Does the routing arithmetic actually behave the way the thresholds say it should?
2. Does it work against real models, not just deterministic mocks?
3. When it revises a claim, does the revision make the claim *better*?

Question 3 turned out to have a more interesting answer than yes/no — see §5.

---

## 2. Layer 1 — Offline unit tests (no network, no API keys)

A standalone script (`test_evolution.py`, kept outside the repo since it's a development artifact, not a shipped test suite) exercises:

- **Evidence-balance arithmetic**: `compute_evidence_balance` at the extremes and at zero.
- **The full routing table**: every (balance, reasoning, verdict) combination mapped to its expected operation, including two adversarial-robustness cases added specifically because they're easy to get wrong — a challenger demanding `needs_reversal` on a claim the evidence still favors gets downgraded to `narrow`, not honored outright; an `unsupported` verdict without dominant refuting evidence gets `narrow`, not `retract`.
- **An end-to-end evolution pass** over a fabricated sub-question with 6 claims and a 5-source evidence pool, using `MockLLMClient` on both sides, checked for: every claim gets challenged, every challenge is logged (including the ones that found nothing wrong — needed later for the self-agreement-bias ablation), revised claims bump version and keep their v1 `original_text`, every non-retract revision gets re-verified so `support_score` never describes stale text.
- **The confidence-formula branch**: claims with no `reasoning_score` reproduce the original two-signal formula exactly (so disabling evolution doesn't silently change historical numbers); challenged claims use the new four-signal formula, which can exceed the old formula's 0.8 ceiling.
- **Evidence sampling** (`select_challenge_evidence`): a synthetic 360-chunk, 9-source pool is sampled down to the configured cap while preserving the claim's cited chunk and spanning all 9 sources — a naive head-of-list slice, by contrast, sees only 1 source.

All of this runs in well under a second and catches routing-logic regressions before a single (paid) API call is made. It was re-run after every subsequent change described below.

---

## 3. Layer 2 — Mock end-to-end pipeline

`python run.py --demo --mock` exercises the full pipeline including evolution, using `MockLLMClient`'s deterministic challenge/revision branches (added specifically for this: `_mock_challenge` spreads outcomes across all four verdicts by hashing the claim text, so mock runs hit every branch of the router, not just "sound"). This is the fast, free regression check — every code change in this document was validated here before spending real money.

---

## 4. Layer 3 — First real-model attempt: dead model slugs

Running the same query against real OpenRouter models (`configs/openrouter.yaml`) failed immediately: `deepseek/deepseek-r1:free` returned 404 — *"This model is unavailable for free. The paid version is available now."* Every `:free` slug baked into the original config (`deepseek-r1:free`, `llama-3.3-70b-instruct:free`) had been retired by OpenRouter since the config was written. A direct probe of `GET /api/v1/models` found 14 currently-live `:free` slugs; `google/gemma-4-26b-a4b-it:free` and `openai/gpt-oss-20b:free` were confirmed working via a raw chat-completion call before wiring them in. `configs/openrouter.yaml` was updated with a comment documenting how to re-check availability, since this will happen again.

Separately, two infrastructure issues were found and are **not** fixed (out of scope for claim evolution, and neither is a code bug): the `.env` `TAVILY_API_KEY` returns 401 directly from Tavily's API (expired/invalid), and `export.arxiv.org` is unreachable from this sandbox — TLS handshake completes, then the connection hangs with no response (confirmed via raw `curl -v`, not just the app's own retry logic). Both mean live retrieval quality was sometimes worse than the pipeline is capable of in a normal environment.

---

## 5. Layer 4 — Real-model integration test via seeded evidence

To test the challenger/reviser against genuine model behavior without depending on the broken retrieval tools, evidence was hand-authored directly into a `ResearchState`: three sources unanimously supporting "RAG reduces hallucination" in round 1, three independent sources complicating that picture in round 2 (noisy retrieval increasing hallucination, a relevance threshold below which RAG doesn't help, models ignoring retrieved context entirely).

This confirmed the mechanism works end-to-end on real models (`google/gemma-4-26b-a4b-it:free` extractor, `openai/gpt-oss-20b:free` challenger) — but also that the free tier is slow enough (single-digit minutes for a 5-claim challenge pass) to make iteration painful, and eventually hit OpenRouter's account-wide 50-requests/day free-tier rate limit mid-run (partly consumed by the model-availability probing in §4). This is where testing moved to cheap paid models.

---

## 6. Layer 5 — Small real query on cheap paid models

Probed pricing (`gpt-4o-mini`: $0.15/$0.60 per M tokens; `deepseek-chat`: $0.26/$1.03 per M) and picked a genuinely different-provider pair: `gpt-4o-mini` for extraction/verification/synthesis, `deepseek-chat` for the challenger. A single-sub-question run on *"Does RAG reduce hallucination in LLMs?"* against **real retrieved evidence** (arXiv worked this time — the earlier hang was transient) produced 23 claims, 1 retraction, and 2 reversals, for **$0.02–0.03**. All three changes read as legitimate: a claim that THaMES "focuses on QA tasks" was correctly retracted once evidence showed it evaluates multiple task types; a claim about TruthfulQA's purpose was correctly reversed once contradicting evidence appeared. This was the first real signal that the mechanism produces *sensible* corrections, not just *some* corrections.

---

## 7. Layer 6 — Full evaluation: all 4 test cases, paid models, `max_challenges_per_round` raised

**Attempt 1 failed on a real constraint, not a bug**: the OpenRouter key was on the free tier ($0.17 lifetime usage, no purchased credits), which caps every paid-model prompt at ~34K tokens regardless of which model is being called. `tc1` (multi-source synthesis across 3 papers) built a 48.9K-token extraction prompt in a single round and got rejected with a 402. Confirmed via `GET /api/v1/key` (`"is_free_tier": true`). Credits were added; `is_free_tier` flipped to `false`; re-ran.

**Configuration**: `gpt-4o-mini` (extractor/verifier/synthesis) vs. `deepseek-chat` (challenger), `max_challenges_per_round=30` (up from the previous cap of 4 — the actual ask that motivated this run), `search_results_per_query=3`, `max_rounds=2`, `adaptive.max_budget=2`. Ran via `eval.harness.run_eval` against the four stock test cases (`eval/test_cases.py`): multi-source synthesis, contradiction tracing, sparse/nuanced retrieval comparison, single-source factual baseline.

### Results

| Test case | Claims | Support rate | ECE | Challenges | Hit rate | Narrow | Reverse | Retract | Support lift |
|---|---|---|---|---|---|---|---|---|---|
| tc1 multi-source | 65 | 95.4% | 0.276 | 73 | 78% | 23 | 26 | 8 | −0.153 |
| tc2 contradiction | 61 | 96.7% | 0.203 | 68 | 88% | 40 | 13 | 7 | −0.047 |
| tc3 sparse/nuanced | 46 | 93.5% | 0.165 | 51 | 84% | 29 | 9 | 5 | −0.040 |
| tc4 factual baseline | 38 | 100.0% | 0.132 | 38 | 55% | 20 | 1 | 0 | **+0.048** |

Total real cost for this run: **$0.50**. Total wall time: ~43,000s (~12 hours — free-tier-adjacent rate limiting on the challenger side, not compute; a run with typical paid-tier throughput would be dramatically faster).

**The good news first**: the difficulty gradient across test cases is exactly what the design predicts. `tc4` (a single well-established paper) has the lowest challenge-hit-rate, the highest average reasoning-soundness (0.85) and evidence-balance (0.70), zero retractions, one reversal, and — uniquely — *positive* support lift. `tc1`/`tc2` (genuinely contested, multi-paper literature) get challenged aggressively and revised most of the time, and `tc2`'s contradiction-detector found **zero** cross-source contradictions in the final report, because per-claim evolution absorbed the disagreement that used to get left as an unresolved pair of claims for the reader to reconcile.

### The finding: `support_lift` was negative for 3 of 4 cases, and `refine` never fired once

Breaking `support_lift` down by operation across all four test cases (161 revisions with valid before/after support scores):

| Operation | n | Mean Δsupport | % improved |
|---|---|---|---|
| narrow | 112 | **+0.045** | 11.6% |
| reverse | 49 | **−0.316** | 8.2% |

`narrow` was working well. Real examples that held support at 1.0 while adding accurate, well-known caveats:
- *"Positional encoding helps transformers... in most transformer models, though variants like ALiBi can function without it."*
- *"BM25 can outperform deep learning models on domain-specific terms for exact term matching and short queries, though deep learning models often excel on semantic tasks."*

`reverse` was the risky operation. Inspecting individual cases surfaced two real failure modes:

- **Metric conflation.** *"LLaMA-Adapter V2 surpasses its predecessor in instruction-following performance"* was reversed because evidence said it *"still cannot generalize well to open-end tasks"* — a different capability than the one the claim was actually about.
- **Silence read as refutation.** *"Chain-of-thought prompting assists in diagnosing flawed conclusions"* was reversed because *"the majority of evidence focuses on reasoning and problem-solving without explicitly linking it to diagnosing flaws"* — the challenger's own prompt explicitly said absence of mention doesn't count as refutation, and it did it anyway.

And `refine_count` was **0 across all 246 revisions in all four test cases.** Its routing condition (balance > 0.5 AND reasoning < 0.6) is a narrow intersection that real multi-paper academic evidence rarely lands in — some source almost always has a caveat, so balance rarely clears 0.5, and everything that should have been "the position is fine, the wording is bad" fell into `narrow` instead.

**The root cause of the `reverse` problem**, found by checking the actual source counts behind the worst cases: `compute_evidence_balance(0, 1) == compute_evidence_balance(0, 10) == -1.0`. Balance is a ratio, and a ratio can't distinguish "one thin, barely-relevant source disagrees" from "ten independent papers disagree." A claim with zero other coverage and exactly one dissenting source got the same reversal authority as one that ten papers actively contradicted.

Full per-test-case state (including every revision's before/after text) is in `eval/results/paid_evolution_run/*/state.json`; aggregate metrics in `eval/results/paid_evolution_metrics.json`.

---

## 8. Fixes implemented in response to §7

Four changes, in `src/agents/challenger.py`, `src/orchestrator/evolution.py`, `src/orchestrator/config.py`, `src/scoring/judge.py` (new), and `src/orchestrator/state.py`:

1. **`min_sources_for_reversal`** (default 2, `EvolutionConfig`). `route_operation` now requires a minimum total source count before `balance < reversal_threshold` can produce `reverse` or `retract`; below it, the claim downgrades to `narrow` instead. (The separate `unsupported + unsound → retract` path is deliberately *not* gated by this — it's a reasoning-failure judgment about the claim itself, not a claim about evidence dominance, so sample size doesn't apply the same way.)

2. **Quote-grounded refutation.** The challenger's output schema changed from bare `refuting_evidence_indices: [int]` to `refuting_evidence: [{index, quote}]`. `_validate_quote_grounding` mechanically checks that each quote is an actual substring of the cited chunk's text before that index counts toward the refuting side. This directly targets the "silence implies refutation" failure: a model can still hallucinate a quote, but it can no longer refute a claim by pointing at evidence that never discusses it, because there is no real text to fabricate a match against. Also added `contested_dimension` (the specific metric/aspect the challenger says is in dispute) to the schema and to the trace, for auditability of the metric-conflation failure mode — logged, not yet hard-gated (see §10).

3. **Flaws-triggered `refine`.** `route_operation` now routes to `refine` whenever the challenger flags a wording-only flaw (`vague`, `conflates_metrics`), independent of the balance/reasoning intersection that made `refine` structurally almost unreachable.

4. **An independent judge** (`src/scoring/judge.py`). `support_lift`'s negative numbers were partly a metric-definition artifact, not a quality regression: a correctly hedged claim is often *harder* to score as fully entailed than the bold, simpler original was, because the hedge itself introduces a claim about variation/conditions that a single cited chunk may not fully cover. The judge asks a structurally different question — given the before/after text, unlabeled and in random order, which one better reflects the full evidence pool? — so it can't inherit that conflation. It reuses the challenger's client (already independent of the extractor) rather than spinning up a third model.

**A bug found while testing the fixes, unrelated to any of the above:** re-running the seeded scenario against real models crashed `verify_claims` with `'list' object has no attribute 'get'` — `gemma-4-26b` returned a bare JSON list instead of the requested object for one call. Every `complete_json` consumer across the codebase (`planner.py`, `extractor.py`, `challenger.py`, `reviser.py`, `judge.py`, `verifier.py`) makes the same assumption. Fixed once at the boundary in `LLMClient.complete_json` (`src/tools/base.py`) rather than in six call sites: a non-dict parse result is now wrapped as `{"_unparsed": ...}`, so every existing `.get(key, default)` degrades to its default instead of crashing.

---

## 9. Verifying the fixes

**Offline** (`test_evolution.py`, extended): the min-sources gate downgrading a thin (1-source) reversal to `narrow` while letting a well-supported (3-source) one through; the `unsupported`-but-unsound retract path proceeding regardless of source count (correct — it isn't gated); the quote-grounding validator keeping a real substring and dropping a hallucinated one, an empty quote, and an out-of-range index; an end-to-end check with a synthetic "silent refuter" LLM (returns the old bare-index shape, pointing at evidence that never mentions the claim) confirming it contributes **zero** refuting sources and a balance of exactly `0.0`, not `-1.0`; the flaws-triggered refine override firing on an otherwise-"keep" case. All pass, alongside the full pre-existing suite (routing table, evolution pass, confidence-formula branching, evidence sampling).

**Mock pipeline**: `run.py --demo --mock` still completes end-to-end after all changes (`MockLLMClient` updated to emit quote-grounded refutations — it now parses real evidence text back out of the rendered prompt so its fabricated quotes are genuine substrings, and gained a judge-comparison branch).

**Real models**: the RAG/hallucination seeded scenario from §5 was re-run on `gpt-4o-mini` / `deepseek-chat` (paid, for speed and reliability over the free tier). All three claims that previously would have been candidates for confident reversal instead produced `narrow` operations that added accurate scope caveats — *"...when the quality of retrieved passages exceeds a certain threshold, though it may increase errors in noisy retrieval contexts"* — while holding `support_score` at 1.0. This is the exact failure pattern from §7, fixed: real, mixed evidence about a claim now gets a caveat instead of an overconfident flip.

---

## 10. What's still open

- **The `evolution` vs. `evolution_self` self-agreement-bias ablation** (DECISIONS.md D021) has not been run to completion with these fixes in place. This is the natural next validation step and was deferred here to avoid unprompted additional spend.
- **A full 4-test-case re-run with the fixes** would give a clean before/after `support_lift` and `judge_improved_rate` comparison at the aggregate level, not just the single re-run in §9. Also deferred for the same reason.
- **`contested_dimension` is logged, not hard-gated.** A model could still cite a same-topic, different-metric quote that happens to share surface wording with the claim. Closing this fully would need embedding similarity or a second LLM check on whether the quote and the claim are actually about the same dimension — a substring check alone can't tell "instruction-following" from "open-ended generalization" apart if a sloppy quote blurs the two.
- **Multi-round convergence is untested.** All real runs so far used `max_budget ≤ 2`, so most sub-questions only got one evolution pass. Whether `stability_rounds` (the freeze-after-N-consecutive-keeps mechanism) actually reduces churn round-over-round, or whether claims keep getting re-litigated indefinitely, is unverified.
- **Cost/value tradeoff of `max_challenges_per_round`.** Raising it from 4 to 30 multiplied token cost roughly 10× per test case (~68K → ~500–690K tokens) for a support-rate improvement of a few points. A middle setting (~10–15, or scaled to claim count) is likely the practical default outside of a deliberate full-evaluation run; the shipped config defaults (`default.yaml`: 12, `openrouter.yaml`: 8) were left as-is since they already sit in that range.

---

## 11. Post-fix validation at scale: 7-test-case suite, multi-round enabled

The two deferred items from §10 (a full re-run with the fixes, and testing multi-round convergence) were run together: the suite was enlarged from 4 to 7 test cases (`eval/test_cases.py` — added `tc5_factual_2`, a second single-source factual baseline; `tc6_active_debate`, a topic with genuinely opposing claims rather than mere qualifications, "are emergent LLM abilities real or a measurement artifact"; `tc7_broad_multiround`, a deliberately broad 5-way comparison meant to push sub-questions toward higher difficulty and more rounds), and `adaptive.max_budget` was raised from 2 to 4 (its actual configured ceiling) to let multi-round evolution actually happen. `max_challenges_per_round` was set to 15 (not the "challenge everyone" cap of 30 from §7 — 7 cases × up to 4 rounds at cap=30 would have been a combinatorial cost blowup). Models unchanged: `gpt-4o-mini` extractor/verifier/synthesis, `deepseek-chat` challenger.

**A second and third real-model bug surfaced before this run completed.** First attempt crashed in the judge with `TypeError: 'NoneType' object is not subscriptable` — `resp.choices[0]` where `resp.choices` was `None`, i.e. `deepseek-chat` returned an HTTP 200 with a genuinely malformed response body (not just wrong-shaped JSON content, an incomplete API response object). Same fix pattern as §8: added `_extract_message_text()` in `src/tools/base.py` that treats a missing/empty `choices` the same as an empty completion, applied to both `complete()` and `complete_json()`, rather than defensively checking in every caller. Full offline suite and mock pipeline re-verified clean before relaunching; the run then completed end-to-end across all 7 cases (~8,251s wall time — much faster than §7's ~43,000s, since paid-tier throughput isn't rate-limited the way the earlier free-tier probing was).

### Multi-round convergence: mostly didn't get exercised, and the reason is informative

Across all 7 test cases (35 sub-questions total), only **one** sub-question ever reached 3 rounds (`tc5_factual_2`, `sq_3`); none reached 4, despite `max_budget=4` being available including for `tc7`, the case deliberately designed to be hard enough to need it. The reason is visible in the difficulty numbers: every sub-question's computed difficulty landed between 0.13 and 0.31, nowhere near the range that would push `allocate_budget`'s linear interpolation (`min_budget + difficulty × (max_budget − min_budget)`) above 2. This is a real, somewhat surprising finding: **the difficulty estimator conflates "hard to find well-supported evidence for" with "hard research question."** `tc7` asks for a 5-way comparison — objectively a harder synthesis task than any single-topic query — but each individual sub-question, once split out, has abundant published literature to draw on, so claims verify with high confidence, which drives the confidence-based half of the difficulty signal (`update_difficulty_from_confidence`, DECISIONS.md D005) back down. A broad-but-well-documented topic and a narrow-but-well-documented topic get nearly the same difficulty score under this signal, even though the synthesis burden differs. This is a limitation of Track A's allocator (already partially flagged in D005/D008.1), not of claim evolution — but it means the current system has no real way to deliberately drive itself into multi-round evolution on demand; it's downstream of a signal that saturates for any topic with decent literature coverage.

Where 2 rounds did happen, keep-rate (fraction of challenges that found nothing wrong) did **not** consistently rise round-over-round — it fell in 4 of 7 cases (e.g. tc1: 23%→18%, tc6: 14%→4%), was roughly flat in 2, and only clearly fell across `tc5`'s 3 rounds (21%→15%→7%). This is *not* necessarily evidence against `stability_rounds` working: each round also runs fresh retrieval, so round 2 isn't re-litigating round 1's evidence, it's responding to genuinely new evidence that round 1 didn't have. Falling keep-rate under a growing, changing evidence pool is defensible behavior, not obviously thrashing. Frozen-claim counts stayed low (0–2 per test case out of 57–134 claims) — expected, since freezing needs two *consecutive* "keep" verdicts, and with most sub-questions capped at 2 rounds, a claim only gets that chance once. **This experiment did not cleanly isolate "does evolution converge when evidence stops changing"** — that would require re-challenging the same claims against a deliberately *frozen* evidence pool across several passes, which is a different (and still open) experiment from what raising `max_budget` actually tested.

### The D022 fixes, measured at scale (not just the one hand-seeded re-check)

| Operation | n (post-fix, 7 cases) | mean Δsupport | % improved | — | n (pre-fix, 4 cases) | mean Δsupport | % improved |
|---|---|---|---|---|---|---|---|
| narrow | 466 | +0.027 | 11.2% | | 112 | +0.045 | 11.6% |
| reverse | 53 | **−0.170** | **18.9%** | | 49 | **−0.316** | **8.2%** |
| refine | 1 | +0.000 | 0.0% | | 0 | — | — |

`reverse`'s average damage to `support_lift` roughly halved (−0.316 → −0.170) and the fraction of reversals that actually improved support more than doubled (8.2% → 18.9%). `narrow` held steady — no regression from the added gating. `refine` fired exactly once across 570 total revisions: the flaws-triggered override works (proven in the offline unit test) but real models rarely emit the specific `vague`/`conflates_metrics` labels needed to trigger it — they tend to jump straight to a verdict like `needs_nuance` instead of naming a wording-only flaw. This gap is real and still open.

**Quote-grounding wasn't a rare-case fix — it was catching something frequent.** Of 715 total challenges, **159 (22.2%) had at least one proposed refuting citation dropped** for failing the substring check. Roughly one in five challenges was proposing to count evidence as refuting that didn't actually say what the challenger claimed. Without this fix, a meaningful fraction of those would likely have pushed claims past the reversal threshold on fabricated grounds.

**The judge is the most reassuring number, and the most important one for resolving §7's central ambiguity.** Across 520 judged revisions: **425 improved (81.7%), 93 worse (17.9%), 2 same.** This directly confirms the hypothesis that `support_lift`'s negative aggregate was partly a metric artifact — a structurally independent, blind pairwise comparison says the large majority of revisions are genuine improvements, even where entailment-strength scoring says otherwise. Read together, `support_lift` and `judge_improved_rate` triangulate the same event two different ways and mostly agree it's a net positive, with `reverse` remaining the operation most likely to still produce a bad outcome roughly 1 time in 5.

### The new test cases

`tc5_factual_2` (LoRA, single well-known technique) and `tc6_active_debate` (emergent abilities: real or measurement artifact) both landed at **100% support rate** with **zero** contradictions detected by the Phase-3 contradiction detector — the same pattern `tc2` showed in §7. `tc6` was designed to test whether a topic with genuinely opposing claims (not just mutual qualification, like CoT) would produce a distinctly higher reversal rate than `tc2`. It didn't, clearly: `tc6` had 8 reversals out of 72 revised claims (11%), barely different from `tc1`'s 13/80 (16%) or `tc5`'s own 9/73 (12%). The likely reason is `min_sources_for_reversal`: even a real, well-known dispute like "are emergent abilities real" surfaces as one or two papers per side in any given sub-question's retrieved pool, and the gate now requires evidence weight to back a reversal regardless of whether the underlying disagreement is "genuine" or "just a qualification" — which is arguably correct (the gate shouldn't have to know the difference), but it means `tc6`'s design hypothesis about reversal *rate* wasn't confirmed by this run, only its presence.

### Updated overall assessment

The two biggest open risks named in the earlier evaluation were: (1) the D022 fixes are unvalidated beyond one hand-seeded re-check, and (2) multi-round behavior is completely untested. (1) is now addressed with real numbers at 7×-larger scale, and the fixes hold up — reversal damage roughly halved, and the independent judge (81.7% improved) is a genuinely reassuring second signal. (2) surfaced something more interesting than a pass/fail: the system currently has no reliable way to *reach* multi-round evolution at all for real, well-documented topics, because the difficulty signal that gates round count saturates low whenever verified confidence is high — independent of how complex the underlying synthesis task actually is. `stability_rounds` remains unvalidated, but for a different reason than before: not because it failed a test, but because the system rarely runs enough rounds to give it anything to do. The self-agreement-bias ablation (`evolution` vs. `evolution_self`) is still the single largest remaining unmeasured claim in the whole feature.

---

## 12. The self-agreement bias test — the answer, and it's not the expected one

Named as the top open item in three consecutive evaluations (D021, D022, D023). Finally run.

**Design change from what was wired up.** `ablations/run_ablation.py` implements this as two full pipeline runs (`evolution` vs `evolution_self`), which confounds the variable of interest — two runs retrieve different evidence and extract different claims, so "challenger identity" is entangled with "different inputs." We ran a **paired** test instead: replay claims and evidence from the completed 7-test-case run, and challenge each *identical* (claim, evidence-pool) pair with both challengers. Only the critiquing model varies. n=105 (15 per test case, seeded). Claims replayed at `original_text`, i.e. as the extractor first wrote them.

| | independent (`deepseek-chat`) | self (`gpt-4o-mini`, = extractor) |
|---|---|---|
| Found fault (verdict ≠ `sound`) | 86/105 (81.9%) | **98/105 (93.3%)** |
| Mean `reasoning_score` | 0.727 | **0.550** |
| Ungrounded refutations dropped | 0.28/claim | 0.30/claim |

Paired: both 86 · **only-independent 0** · **only-self 12** · neither 7 · McNemar exact **p = 0.0005**.

**The hypothesis is refuted as a first-order effect.** Self-agreement bias predicts the self challenger goes easier on its own claims. It did the opposite — strictly harsher, with *zero* claims out of 105 where only the independent challenger objected. It also scored reasoning lower on 80 claims and higher on only 8. All 12 discordant cases were `needs_nuance` on claims the independent challenger had rated `sound` at reasoning 0.9–1.0.

**The confound, stated plainly.** This isolates the input, not the model. It cannot separate "gpt-4o-mini doesn't go easy on itself" from "gpt-4o-mini is just a harsher critic than deepseek-chat." The definitive version is a **2×2 crossover** — each model challenging both its own and the other's claims — where bias is the *interaction* term. What this run does establish is that the bias, if it exists, isn't strong enough to overcome baseline harshness differences, which is the practically relevant question for choosing a challenger.

**A result that cuts against the harsher critic.** The largest gap was on `tc4_factual` (60% vs 86.7% fault, −26.7pp) — the settled-facts baseline, where quiet behavior is the *correct* behavior. A challenger objecting to 87% of well-established Transformer facts reads as noise, not rigor. Read alongside §11's finding that "found more fault" ≠ "is right," the more lenient independent challenger looks better calibrated here.

**Design consequence.** D021's rationale — independence as "the structural defense" against self-ratification — is contradicted, not merely unverified. Independence may still be justifiable (not sharing the extractor's specific blind spots), but the stated mechanism is not what the data shows. Challenger choice *does* matter materially — routed-operation agreement was only 75.2%, dominated by `keep→narrow` disagreements — just not in the predicted direction.

**Incidental validation.** Both models proposed ungrounded refutations at nearly identical rates (0.28 vs 0.30/claim). The D022 quote-grounding fix is doing real work independent of provider.

---

## 13. Query reformulation and report-level self-correction (built, mock-verified)

Two features added in response to the §11/§12 findings; both offline- and mock-verified, neither yet measured on real models at eval scale.

**Query reformulation** (`src/agents/query_reformulator.py`) fixes a mechanical defect found by inspection: `research_sub_question` passed `sq.question` **verbatim** to arXiv and web search on *every* round (`researcher.py:39,60`), so round 3 issued the identical query round 1 did and only paged deeper into the same ranked list. Extra rounds bought volume, not new angles — a direct contributor to §11's finding that accumulating rounds barely moved confidence. Round 1 now uses the sub-question; rounds 2+ target what earlier rounds missed. **Compaction is the load-bearing choice**: the reformulator never sees the raw evidence pool (hundreds of chunks, growing every round) — only a digest of `SubQuestion.retrieval_attempts` (prior queries, returned source titles, chunk counts) plus the weakest standing claims. Guards against both degenerate outputs (empty query; echoing an already-tried query) with a mechanical check and logged fallback. Verified in a forced multi-round mock run: r1 verbatim, r2/r3 distinct with recorded gaps, full trajectory in `state.json`.

**Report-level self-correction** (`src/orchestrator/report_loop.py`, `src/agents/report_critic.py`) is Phase 5 — the only stage that asks whether the *assembled report* answers the question asked, a gap ARCHITECTURE.md §12.5 and D016 both predicted. **Two tiers of error detection**: Tier 1 `mechanical_checks()` is deterministic and LLM-free (retracted claim still asserted in the report via conservative 0.8 word-overlap; sub-question with zero surviving claims; thin-evidence sub-questions; contradictions detected but never discussed), and its findings are handed to the LLM critic as *established facts* so the model spends attention on judgment calls instead of re-deriving what a substring check proved. Tier 2 is an independent critic emitting `accept` / `revise_report` / `needs_more_research`.

**The two features are one loop.** A `needs_more_research` gap's `what_to_find` is written into the target sub-question's `retrieval_attempts[-1].gap_noted`, which the reformulator consumes — so re-research searches for what the *critic* diagnosed as missing rather than blindly re-running. That is the Search-R1 "learn from retrieval mistakes" idea closing end-to-end: critic supplies the gap, reformulator turns it into a query, `retrieval_attempts` is the compacted memory of what was already tried.

**Three brakes**, because a loop that can reopen retrieval is the most expensive thing in the pipeline: `max_passes` (2); **one reopen per sub-question ever** (afterwards, rewriting only); and `stop_when_not_improving`, which halts when a pass fails to reduce high-severity defects — directly motivated by §11's finding that a critic finding more fault isn't evidence it's right. A `needs_more_research` verdict naming no actionable gap is downgraded rather than triggering a directionless reopen.

Mock end-to-end: pass 1 diagnosed a buried answer + overstatement, reopened research on a named sub-question (claims 9 → 12, real new evidence gathered and evolved), re-synthesized to v2; pass 2 accepted.

**A third real-model boundary bug** surfaced during this work: `resp.choices` was `None` from `deepseek-chat` — HTTP 200 with an incomplete body, not merely malformed JSON content. Fixed once at the boundary (`_extract_message_text` in `src/tools/base.py`) for both `complete()` and `complete_json()`. Three such bugs now, all only reachable under real API load — the mock client can simulate content-level malformation but not infrastructure-level.

**Still unmeasured:** whether reformulated rounds actually retrieve more *diverse sources* than verbatim re-runs (measure: inter-round source overlap, with and without); how a real critic behaves at eval scale (first-pass accept rate, whether reopened research improves the report or just adds volume); and whether the mechanical tier catches defects the LLM critic misses, which is the claim justifying its existence.

---

## 14. Frozen-pool convergence: the loop does not converge, and freezing was hiding it

Every multi-round result up to this point was confounded: each round also ran fresh retrieval, so "claims kept changing" could mean *the loop doesn't converge* **or** *new evidence legitimately kept arriving*. Section 11 flagged this as unresolved. This experiment isolates it — **freeze the evidence pool** and re-challenge the same claims against the same evidence for 5 passes.

**Two conditions, because the single-condition version would have been self-deceiving.** `stability_rounds=2` freezes any claim surviving two consecutive challenges, which makes churn *look* bounded almost by construction. So:

- **A** — `stability_rounds=2` (production default)
- **B** — `stability_rounds=999` (freezing effectively disabled)

12 claims (6 from `tc4_factual`, 6 from `tc2_contradictory` — settled vs. contested), `gpt-4o-mini` extractor/reviser, `deepseek-chat` challenger.

| pass | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| **A** revisions | 5 | 6 | 5 | 6 | 5 |
| **A** keep-rate | 58.3% | 50.0% | 16.7% | **0.0%** | 16.7% |
| **A** frozen | 0/12 | 6/12 | 6/12 | 6/12 | 6/12 |
| **B** revisions | 6 | 6 | 4 | 6 | 5 |
| **B** keep-rate | 50.0% | 50.0% | 66.7% | 50.0% | ~50% |
| **B** frozen | 0 | 0 | 0 | 0 | 0 |

**Neither condition converges.** Against evidence that never changed, claims are rewritten indefinitely at a steady ~50% keep rate, with no downward trend over five passes.

**6 of 12 claims oscillate** — their text returns to a value it already held (A→B→A). That is cycling, not refinement: `narrow` → `reverse` → `narrow` back. Raw revision counts cannot distinguish this from steady improvement, which is exactly why oscillation was instrumented separately.

**`stability_rounds` is a circuit breaker, not a convergence mechanism.** In condition A, 6 claims froze — and the *remaining* 6 dropped to a **0% keep rate by pass 4**, i.e. changing on essentially every challenge. Freezing removed claims from observation rather than settling them. Condition B, with freezing off, shows the underlying behaviour plainly: perpetual churn. The prior reading in §11 — that falling keep-rates were a defensible response to genuinely new evidence — does not survive this: the same pattern appears with no new evidence at all.

*(A note on the harness: the script's auto-printed verdict line concluded "the underlying process settles on its own." That is wrong — it compares final revision counts between conditions and falls through to an else-branch when neither converges. The table above is the correct reading. Left as a caution about trusting a summary line over the data it summarises.)*

### This invalidated part of the scheduler written the same day

The new scheduler (§15) scores allocation partly on **observed yield** — did the last round change any standing claim? Oscillating claims change *every* round, forever. A yield signal counting "claims changed" therefore reads perpetual thrash as perpetual productivity, and the scheduler would have poured its entire pool into the one sub-question least able to use it.

Caught only because the experiment ran *before* the scheduler was trusted. Fixes:

1. **Oscillation detection** in `evolution.py`: each claim keeps a `text_history` of wording fingerprints; a revision returning to a previous fingerprint sets `oscillating=True` and freezes the claim immediately — no waiting for `stability_rounds`.
2. **Oscillation as a negative signal** in `scheduler.py`: the yield term is scaled by `(1 − oscillating_fraction)`. Cycling suppresses spending instead of attracting it, on the reasoning that more retrieval does not resolve a genuine conflict in the literature.
3. **Oscillation surfaced in the report**, not suppressed. A claim that cannot settle under repeated challenge is a *finding*: the evidence does not determine the answer. Reporting whichever version the last pass happened to land on would present a coin-flip as a conclusion. The confidence index now carries an "Unresolved under repeated scrutiny" section.

That last point is the reframe worth keeping: **oscillation is diagnostic, not merely a bug**. The system now distinguishes three honest states — supported, retracted, and *genuinely contested* — where before it had only the first two and would silently emit an arbitrary reading of the third.

---

## 15. Alternate effort strategy: global scheduler (`adaptive.strategy`)

Added as a **parallel strategy behind a config flag**, not a replacement, so both remain runnable and directly comparable: `adaptive.strategy: "threshold" | "scheduler"`, defaulting to `threshold`.

**Why.** §11 established that the threshold allocator can effectively never grant a third round: budget 3 requires difficulty ≥ 0.667, difficulty updates as `0.6·(1−avg_confidence) + 0.4·linguistic`, so clearing that bar at typical linguistic difficulty needs **average claim confidence ≤ 0.09**. Observed median confidence was 0.85; observed difficulty spanned 0.13–0.31; **0 of 35 sub-questions ever crossed the threshold**. Multi-round research was unreachable in practice, so the convergence question could not even be posed from a normal run.

**The diagnosis: threshold calibration, not signal quality.** Difficulty discriminated fine — 0.13 vs 0.31 is a real 2.4× spread. It simply never cleared an arbitrary absolute bar, and rescaling the bar only relocates the problem.

**The fix: rank instead of threshold.** Ask *"which sub-question most deserves the next round?"* rather than *"does this sub-question deserve more rounds?"* An argmax has nothing to calibrate — even when every difficulty sits in [0.13, 0.31], ranking still allocates differentially. The saturation problem disappears by construction, and **the difficulty formula is left completely unchanged**.

Design:
- A single **global `total_round_pool`** replaces per-sub-question budgets, making total cost a direct knob rather than an emergent consequence of per-item thresholds. `pool == n_sub_questions` reproduces the uniform baseline exactly, so the ablation baseline becomes a *parameter* rather than a separate code path.
- **Mandatory cold-start pass** over every sub-question — marginal value cannot be estimated before seeing what a sub-question returns. This is the only honest remaining job of the pre-retrieval linguistic estimate: ordering that sweep.
- `marginal_value = uncertainty × yield × (1 − oscillation) × coverage_deficit`, a **product** rather than a weighted sum so that any single near-zero term correctly vetoes the allocation instead of being averaged away.
- **Uncertainty uses spread, not just the mean.** Claims at 0.9 and 0.3 are unresolved in a way a uniform 0.6 is not; the old mean-only signal was blind to that.
- The pipeline's round body is **shared verbatim** between both strategies, so they differ only in scheduling.

**Verified in mock:** the scheduler allocated **4 / 2 / 2** rounds across three sub-questions (not uniform 3/3/3), with monotonically decreasing marginal values (0.31 → 0.29 → 0.16 → 0.10), and **stopped early with pool unspent** once nothing cleared the floor. One sub-question reached **4 rounds** — against 0-of-35 exceeding 2 under the threshold strategy.

**Not yet done:** no real-model comparison of the two strategies. The Track A "3.5× cheaper" result is currently attributed to difficulty-based allocation and would need re-measuring as convergence-based scheduling before the README claim covers this path. The uniform baseline being reachable as `pool == n_sub_questions` makes that a clean three-way comparison when run.

---

## 16. Challenger memory: the fix that didn't work, and the pattern that did

D028 proposed that oscillation was a **memory** problem — the challenger is stateless across passes, so it can argue a claim back to a wording it abandoned two passes ago without ever knowing. §15's next-step list named this the one research item worth doing.

Built it: the challenge prompt now carries the claim's recent revision history (original wording, each operation, each rationale) plus an explicit escape hatch — *if your objection would return this claim to a wording it already held, set `contested_stalemate` instead*. That verdict routes to `keep`, freezes the claim, and marks it contested. Config-gated (`evolution.challenger_sees_history`) so memory on/off is a controlled A/B on the same frozen-pool harness.

| | memory off | memory on |
|---|---|---|
| revisions per pass | [6, 5, 6, 7, 5] = **29** | [6, 5, 6, 5, 5] = **27** |
| oscillating claims | **6** | **6** |
| stalemates declared | — | **0 of 60** |

**Refuted.** 29 vs 27 on n=12 is noise; oscillation is byte-identical at 6. Given its own history and an explicit way to say "the evidence doesn't settle this," the challenger used it **zero times in sixty opportunities**. The plumbing was verified separately — `_format_revision_history` renders correctly with revisions present and returns empty when disabled — so this is refusal, not a bug.

**The third instance of one lesson.** Prompting could not enforce: (1) "silence is not refutation" — 22% ungrounded refutations anyway; (2) "emit a confidence marker on every claim" — zero emitted; (3) "declare a stalemate instead of re-litigating" — zero declared. Every time, the property only held once it moved into code. Here the code fix already existed and dominates the prompt fix, which is why the feature still ships: cheap, occasionally helpful, and not load-bearing.

**The more interesting reading.** The challenger's prompt ends *"Do not be agreeable."* We instructed it to always find fault, so a stalemate is a concession against its assigned role. Oscillation may therefore be a **role failure rather than a memory failure** — an agent told to find what's wrong will find something wrong, forever. Testable by softening the adversarial framing and re-running the same harness; not done.

*(Harness caution, second occurrence: the script's auto-printed verdict read "reduces churn modestly; partially a memory problem" — purely an artifact of a hard-coded threshold falling through to a middle branch. The table is the reading. Both convergence scripts have now produced a summary line more confident than its own data, which is its own small lesson about generated conclusions.)*
