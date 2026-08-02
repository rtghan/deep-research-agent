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
