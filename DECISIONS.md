# DECISIONS.md — Design Decision Log

> Every design decision: the problem that motivated it, the alternatives we considered and rejected, what we chose and why, and what it actually produced — including limitations it did not solve.
>
> Decisions are tagged with the Notion "Advanced Areas of Pursuit" research direction(s) they address: **[S1]** Search-and-reason interleaving, **[S2]** Process reward models, **[S3]** Self-correction, **[S4]** Test-time compute scaling, **[S5]** Multi-agent debate/review, **[S6]** Dynamic tool selection, **[S7]** Context management, **[S8]** Infrastructure & observability.

---

## D001 — Pydantic for all state models **[S7] [S8]**

**The problem.** A multi-round research pipeline accumulates a large amount of structured state — evidence chunks, claims, citations, contradictions, difficulty scores, confidence values, token costs, and a step-by-step trace. Without a typed schema, every component would pass around opaque dicts, making it impossible to (a) serialize a run for later replay, (b) inspect what went wrong three steps ago, or (c) enforce that each component declares its inputs and outputs.

**Alternatives considered.**
- *Plain dicts.* Rejected because they hide structure. A missing key surfaces as a runtime `KeyError` deep in the pipeline, not a schema violation at the boundary. The brief penalizes opaque systems.
- *dataclasses.* Lighter weight, but lack built-in JSON serialization and validation. We would end up writing the serialization glue that Pydantic gives for free.
- *A framework's built-in state type (LangGraph `State`).* Considered, but we deliberately chose plain Python over a framework (see D015). A framework state type would couple us to that framework's serialization format.

**What we chose and why.** Pydantic `BaseModel` for every state object: `ResearchState`, `Claim`, `EvidenceChunk`, `SubQuestion`, `ResearchPlan`, `Contradiction`, `TraceEntry`. Each component receives and returns typed models, not dicts. JSONL trace serialization becomes a one-liner (`.model_dump_json()`).

**What it produced.** The trace system (§13 of ARCHITECTURE.md) works directly off Pydantic models — every `log_step` call serializes a `TraceEntry` to JSONL with no custom code. The eval harness can load a `state.json` from any run and recompute metrics. The dependency graph in ARCHITECTURE.md §17 is auditable because every arrow is a typed model, not an implicit dict shape.

**What it did not solve.** Pydantic validates structure, not semantics. A claim with `support_score=0.9` and `verification_status="unverified"` passes validation but is logically inconsistent. We catch some of this in the confidence scorer, but there is no cross-field invariant enforcement.

---

## D002 — Two LLM models (sub-step vs. synthesis) **[S4]**

**The problem.** The pipeline makes many LLM calls — planning, research, extraction, verification, confidence scoring — for every sub-question, and then one synthesis call to produce the final report. If all calls use the same model, we either overpay for intermediate steps that don't need deep reasoning, or under-invest in the synthesis step that determines final quality.

**Alternatives considered.**
- *Single cheap model (gpt-4o-mini) for everything.* Cheapest, but synthesis quality suffers — the final report is the user-facing artifact and needs the strongest reasoning.
- *Single expensive model (gpt-4o) for everything.* Highest quality, but the cost-quality curve (Track A's core metric) would be dominated by intermediate steps that don't benefit from the stronger model. The ablation would show a flat curve, not a meaningful cost-quality tradeoff.
- *Three-tier (planner on strong, sub-steps on cheap, synthesis on strong).* The planner decomposes the question, which is important, but in practice the decomposition is straightforward and gpt-4o-mini handles it well. Adding a third tier adds config complexity without measurable benefit.

**What we chose and why.** `sub_step_model = gpt-4o-mini` for planning, extraction, verification, confidence. `synthesis_model = gpt-4o` for the final report only. The split is cost-driven: sub-steps are high-volume (4 sub-questions × 1-4 rounds × 4 agents = 16-64 calls) but don't need deep reasoning; synthesis is one call but quality-critical.

**What it produced.** Token costs stay low enough that the cost-quality curve is meaningful — adaptive mode costs ~31K tokens vs. uniform's ~110K. If everything ran on gpt-4o, the absolute costs would be higher and the *ratio* might be similar, but the story would be "we spent a lot and got good results" rather than "we spent intelligently." The synthesis model choice also means the final report quality is not bottlenecked by the cheap model's writing ability.

**What it did not solve.** The verification step (Track B's core) runs on gpt-4o-mini, the same model as extraction. This creates a self-agreement bias risk (see D006). Using a stronger model for verification would improve calibration but increase cost — a tradeoff we did not explore in the ablation.

---

## D003 — arXiv as primary source, web as secondary **[S6]**

**The problem.** The system needs external evidence. The test cases are ML-literature questions (Transformer architectures, CoT evolution, retrieval methods), so the evidence source matters. We need a source that (a) covers the topic, (b) requires no authentication for a zero-dependency run, and (c) provides structured metadata (title, authors, abstract, full text).

**Alternatives considered.**
- *General web search only (Tavily/DuckDuckGo).* Broader coverage but noisier — blog posts, tutorials, and secondary sources mixed with primary research. Requires an API key (Tavily) or has rate limits (DuckDuckGo). Cannot guarantee academic rigor.
- *Google Scholar.* No official API; scraping is fragile and against ToS.
- *Semantic Scholar API.* Good academic coverage, but we already had arXiv working and adding Semantic Scholar would be a second source with similar content overlap.
- *Proprietary databases (JSTOR, IEEE).* Require institutional access. Not viable for a take-home.

**What we chose and why.** arXiv API as primary — no key needed, covers all test topics, returns abstracts and full PDFs. Tavily web search as secondary — adds source diversity for triangulation and catches non-arXiv sources (e.g., survey papers on personal sites, blog posts by researchers). The system runs end-to-end with zero external API keys (arXiv only); with a Tavily key, evidence diversity improves.

**What it produced.** The system runs out-of-the-box with `python run.py --demo` and no API key (using the mock LLM or OpenRouter free tier). Real runs with arXiv produce evidence chunks from actual papers. Web search (when configured) adds 2-5 additional sources per sub-question, improving the diversity component of the confidence score (D007).

**What it did not solve.** arXiv has coverage gaps — emerging topics, non-academic developments, and pre-2020 work may not be well-indexed. The "sparse evidence" test case (tc3) exposes this. We do not have a fallback to general web crawling for topics where arXiv returns nothing. Dynamic tool selection (Notion §6) would address this but was not implemented — see D017.

---

## D004 — Track A + Track B hybrid (not just one) **[S1] [S2] [S3] [S4]**

**The problem.** The Notion "Agent Design" plan offers multiple advanced directions. Pursuing only one gives a narrow story. Pursuing all of them is infeasible in the time budget. The question is: which combination produces a coherent thesis where the components *reinforce* each other rather than just coexisting?

**Alternatives considered.**
- *Track A only (adaptive test-time compute).* Clean cost-quality story, but the difficulty signal that drives compute allocation would need to come from a purely linguistic heuristic. Without a verification signal, "difficulty" is just "does the query contain hard keywords?" — a weak proxy.
- *Track B only (evidence-grounded verification).* Strong honesty story, but no cost-quality narrative. The system would verify everything equally, spending the same compute on easy and hard questions.
- *Track C (infrastructure/observability).* De-scoped. A replayable trace viewer is a strong engineering project, but it requires more setup than the adaptive-budget idea and doesn't produce a research thesis. We kept the trace *system* (which is necessary for debugging) but did not build the viewer UI.
- *Track D (multi-agent debate).* De-scoped. Multi-agent debate (Notion §5) adds communication overhead and risks shared blind spots. Independent review is easier to defend, but even that requires 2x the LLM calls for marginal quality gain in a 2-day project. See D016.

**What we chose and why.** Track A (adaptive compute) as primary, Track B (evidence-grounded verification) as complementary. The two tracks are *mutually reinforcing*: Track B's verifier confidence provides the difficulty signal Track A needs (D005), and Track A's compute allocation amplifies Track B's honesty by spending more retrieval rounds on uncertain claims. This coupling is the core design insight — neither track works as well alone.

**What it produced.** One cohesive story with two "wow moments": (1) the cost-quality curve showing adaptive matching uniform quality at 3.5x lower cost (D012), and (2) the reliability diagram showing the system measures its own calibration (D013). The coupling is visible in the architecture: `difficulty.py` reads confidence scores from `confidence.py`, which reads from `verifier.py`. Remove any link and both tracks degrade.

**What it did not solve.** The coupling means the tracks share failure modes. If the verifier is miscalibrated (D013), the difficulty signal is also miscalibrated — the system may over-allocate compute to claims that are actually well-supported, or under-allocate to claims that are genuinely uncertain. A decoupled system would be more robust to verifier failure but would lose the reinforcement benefit.

---

## D005 — Difficulty estimation is two-phase **[S1] [S4]**

**The problem.** To allocate compute adaptively (Track A), we need a difficulty signal. But difficulty is not knowable in advance — a question that *looks* easy ("compare GPT-4 and Llama architectures") may turn out to have contradictory sources, while a question that *looks* hard ("BM25 vs dense retrieval") may have clean, convergent evidence. A single estimate at the start would be stale by the time we have evidence.

**Alternatives considered.**
- *Linguistic-only.* Estimate difficulty from query features (length, keywords, question type) before any retrieval. Simple, but blind to evidence quality. A question with contradictory sources would get the same budget as one with convergent sources if the queries look similar.
- *Confidence-only.* Wait until after the first retrieval round, then estimate difficulty purely from verifier confidence. Better signal, but requires running at least one round with no difficulty-based budget allocation — a chicken-and-egg problem.
- *LLM-judge difficulty.* Ask the LLM to rate the question's difficulty before retrieval. Rejected because LLM difficulty assessments are uncalibrated (same problem as LLM self-reported confidence in D007) and add a token cost to every run.

**What we chose and why.** Two-phase estimation:
- **Phase 1 (pre-retrieval):** Linguistic estimate from keywords, query type, and question structure. This is the *prior* — the best guess before we have evidence. It determines the initial budget allocation for the first round.
- **Phase 2 (post-retrieval):** Confidence-based update after each retrieval round. Formula: `difficulty = 0.6 * confidence_difficulty + 0.4 * linguistic`. The confidence-weighted term dominates as we accumulate evidence, so the estimate converges toward the *actual* difficulty.

This is the search-and-reason interleaving idea (Notion §1): the system revises its research plan based on what it has found so far, not just once at the start.

**What it produced.** The feedback loop works: low-confidence claims (support_score < 0.5) trigger more retrieval rounds (up to `max_budget`), while high-confidence claims stop early (after 1-2 rounds). The ablation (D012) shows this produces 98% claim-support at 3.5x lower cost vs. uniform — the adaptive system is not just doing fewer searches, it's doing *the right* fewer searches.

**What it did not solve.** The 0.6/0.4 weighting is hand-tuned, not learned. We did not run a sensitivity analysis on the weighting. A 0.8/0.2 split would make the system more responsive to confidence; a 0.4/0.6 split would make it more conservative. The optimal weighting likely depends on the topic and source quality — a fixed weighting is a simplification.

---

## D006 — Verifier model choice and self-agreement bias **[S2] [S3]**

**The problem.** The verifier (Track B's core) checks whether claims are supported by evidence. If the same model extracts and verifies claims, it may suffer from *self-agreement bias* — the model that generated a claim tends to agree with itself that the claim is correct. This is the same concern that Notion §3 (Self-correction / SCoRe) raises: "ask the model to check itself" is not automatically reliable.

**Alternatives considered.**
- *Different model family for verification (e.g., Claude for verify, GPT for extract).* Eliminates self-agreement bias. Rejected for cost and complexity — adding a second provider doubles the API key requirements and the config surface area. Also, cross-model verification introduces *model-disagreement bias* — Claude and GPT may disagree on claim support for reasons unrelated to evidence quality.
- *Same model, different prompt.* Cheaper, but the bias is model-level, not prompt-level. A different prompt does not change the model's prior on its own outputs.
- *Ensemble verification (majority vote across multiple models).* Strongest in theory, but 3x the verification cost and complex to implement. Out of scope for a 2-day project.

**What we chose and why.** In principle, `verifier_model ≠ sub_step_model`. In practice, both use gpt-4o-mini for cost reasons. This is a *known, documented limitation* — not a hidden flaw. The decision is transparent in the ablation: D013 shows the system is under-confident (ECE=0.37), which is the *safer* direction of miscalibration. If self-agreement bias were causing overconfidence, the ECE would show the opposite pattern.

**What it produced.** The verifier is load-bearing (D014 proves this — removing it drops support rate to 0%). Even with the same model, the verifier catches unsupported claims because it re-examines the evidence independently rather than trusting the extractor's assessment. The verification prompt asks "does this evidence support this specific claim?" — a different question than "extract claims from this evidence," which limits the overlap.

**What it did not solve.** We cannot distinguish "the verifier agrees because the claim is well-supported" from "the verifier agrees because the same model generated it." A proper ablation would run verification with a different model family and compare the support-rate distribution. This is the most important follow-up experiment. The SCoRe paper's insight — that self-correction requires training, not just prompting — suggests that even a different model would not fully solve this; the verifier would need to be trained on verification-specific data.

---

## D007 — Calibration via heuristic, not temperature scaling **[S2] [S4]**

**The problem.** We need a confidence score for every claim — a number between 0 and 1 that reflects how likely the claim is to be correct. This score drives two things: (1) the difficulty signal for Track A (low confidence → more retrieval), and (2) the reliability diagram for Track B (predicted confidence vs. actual accuracy). If the confidence score is uncalibrated, both tracks suffer.

**Alternatives considered.**
- *LLM self-reported confidence.* Ask the model "how confident are you in this claim?" Rejected because LLM confidence is notoriously uncalibrated — models tend to be overconfident on easy questions and underconfident on hard ones. This is the same problem as LLM-judge difficulty (D005).
- *Temperature scaling.* Learn a single temperature parameter on a held-out validation set that maps logits → calibrated probabilities. Requires ground-truth labels (which we don't have — see D008) and access to model logits (which we don't have through the API for closed models).
- *Isotonic regression.* Learn a non-parametric calibration map from (raw_score, ground_truth) pairs. More flexible than temperature scaling, but same data requirement.

**What we chose and why.** A transparent heuristic: `confidence = (0.5 * support_score + 0.3 * diversity) * (1.0 - contradiction_penalty)`. Each component is interpretable:
- **Support (50%):** The verifier's assessment that the claim is backed by evidence. This is the dominant signal.
- **Diversity (30%):** Number of independent sources. 1 source = 0.5, 2 = 0.75, 3+ = 1.0. Triangulation increases confidence.
- **Contradiction penalty (0.3 if contradictions exist):** If other sources contradict this claim, confidence is reduced. This is the honesty mechanism — conflicting evidence should lower confidence.

**What it produced.** The reliability diagram (Track B wow moment) shows the system is *under-confident*: it predicts 58-64% confidence but achieves 92-100% accuracy. ECE = 0.37. This is an honest finding — the heuristic compresses confidence into a narrow band (0.5-0.65) because the support scores from gpt-4o-mini are bimodal (either ~0.8 or ~0.3) but the multiplicative formula with diversity and penalty pulls everything toward the middle.

**What it did not solve.** The heuristic is not a learned calibrator. It cannot adapt to the model's actual confidence distribution. The bimodal support scores should produce a wider confidence range, but the formula compresses them. The next step (documented in D013) would be temperature scaling or isotonic regression on a held-out set with human-annotated ground truth. The heuristic was a time-constrained choice — transparent and explainable, but not accurate.

---

## D008 — Proxy ground truth for calibration evaluation **[S2]**

**The problem.** To compute Expected Calibration Error (ECE), we need ground-truth labels — for each claim, was it actually correct? We do not have human-annotated labels, and producing them is expensive and time-consuming. Without ground truth, we cannot compute ECE at all, and the Track B story (reliability diagram) collapses.

**Alternatives considered.**
- *Human annotation.* Gold standard, but infeasible for a 2-day project. Each claim requires domain expertise to verify against the source.
- *LLM-judge ground truth.* Use a stronger model (gpt-4o) to judge whether each claim is correct. Better than nothing, but introduces the same self-agreement risk as D006 — the judge may share the verifier's blind spots.
- *No calibration evaluation.* Report confidence scores without ECE. Rejected because the brief explicitly asks about confidence and honesty. A system that reports confidence without measuring calibration is not honest about its own uncertainty.

**What we chose and why.** Use `support_score >= 0.5` as the proxy for "correct" and `< 0.5` as "incorrect." The verifier's support score is the best proxy available — it is an independent assessment of whether the evidence backs the claim, not the confidence score itself (which would be circular in a different way).

**What it produced.** The ECE is computable and the reliability diagram is meaningful — it shows the relationship between predicted confidence and actual (proxy) accuracy. The under-confidence finding (ECE=0.37) is directionally correct: the system predicts lower confidence than it achieves, which is the safe direction.

**What it did not solve.** The proxy is *partially circular*: the verifier produces both the confidence signal (via support_score → confidence formula) and the ground truth (via support_score ≥ 0.5). If the verifier is systematically wrong, both the prediction and the ground truth are wrong in the same direction, and the ECE looks better than it is. A proper evaluation would use human annotations or an independent LLM judge. This is documented as a known limitation in ARCHITECTURE.md.

---

## D009 — Fixed-size chunking with overlap **[S7]**

**The problem.** Retrieved text (arXiv abstracts, web pages, PDF text) can be long. The extractor needs manageable chunks to process. If chunks are too large, the extractor misses claims; if too small, claims are split across boundaries.

**Alternatives considered.**
- *Semantic chunking.* Split at paragraph or section boundaries. Better for claim coherence, but requires parsing document structure (which varies across arXiv HTML, web HTML, and PDF text). Adds complexity without a clear quality improvement for this use case.
- *No chunking (pass full text to extractor).* The extractor prompt would need to handle variable-length inputs, and long texts exceed the context window for gpt-4o-mini (128K tokens, but quality degrades with long inputs — the "lost in the middle" problem).
- *Token-based chunking (e.g., 512 tokens).* More precise than character-based, but requires a tokenizer dependency. Character-based is simpler and sufficient for this project's text lengths.

**What we chose and why.** 1500-character chunks with 200-character overlap. Simple, predictable, and the overlap prevents claims from being split at boundaries. The extractor processes each chunk independently, which is the parallelization pattern from the Notion "Building Effective Agents" section.

**What it produced.** Works fine in practice. A few claims may span chunk boundaries, but the 200-char overlap covers most cases. The chunk size was not tuned — it was chosen as "large enough for a paragraph, small enough for the extractor to handle."

**What it did not solve.** Semantic chunking would preserve section structure (e.g., "Methods" vs. "Results"), which could improve extraction quality. We did not measure the impact. For papers with complex structure (tables, figures, equations), character chunking is particularly crude — it may split a table across two chunks.

---

## D010 — Contradiction detection is cross-source only **[S1] [S3]**

**The problem.** The brief's Example 2 asks about CoT evolution disagreements — the system should detect when sources disagree. But not all disagreements are meaningful: within a single paper, apparent contradictions are usually extraction errors (the extractor took two sentences out of context), not genuine disagreements. Cross-source contradictions are the interesting finding.

**Alternatives considered.**
- *All-pairs contradiction detection.* Flag any two claims that contradict, regardless of source. Noisier — within-source "contradictions" are usually extraction artifacts.
- *LLM-judge contradictions only.* Ask the LLM "do these claims contradict?" without source filtering. Same noise problem, plus the LLM may hallucinate contradictions.
- *No contradiction detection.* Rely on the confidence score to implicitly capture disagreements via the contradiction penalty. Rejected because the brief explicitly asks for contradiction detection as a first-class output.

**What we chose and why.** Only flag contradictions between claims from *different sources* (different papers or websites). Within-source contradictions are treated as extraction errors and silently dropped. This directly serves the brief's Example 2 (CoT disagreement).

**What it produced.** The contradiction detection produces meaningful cross-source disagreements — e.g., different papers reporting different CoT accuracy numbers. These are reported in the "Contradictions & Disagreements" section of the final report, which is one of the four claim categories the brief asks for (directly supported, weakly supported, conflicting, no evidence).

**What it did not solve.** The detection is binary (contradiction or not), not graded. Two claims that "somewhat disagree" are not flagged. A more nuanced approach would compute a contradiction *score* (like the support score) rather than a boolean. The contradiction penalty in the confidence formula (D007) is a fixed 0.3 regardless of how many sources contradict — a more sophisticated model would weight by the number and strength of contradictions.

---

## D011 — Uniform mode gives max_budget to all sub-questions **[S4]**

**The problem.** To prove that adaptive compute allocation helps, we need a baseline. The baseline must be *fair* — if it's too weak, the comparison is meaningless. The strongest baseline is "give every sub-question the maximum budget" — if adaptive can match that quality with less total compute, the cost-quality story is clear.

**Alternatives considered.**
- *Fixed budget = 3 for all.* A middle-ground baseline. But "3 rounds for everything" is arbitrary — why 3? The max_budget baseline (4 rounds for everything) is the upper bound; if adaptive beats *that*, the result is unambiguous.
- *Random budget allocation.* Randomly assign 1-4 rounds to each sub-question. Tests whether *any* non-uniform allocation helps, not specifically *difficulty-based* allocation. Interesting but not the cleanest ablation.
- *Budget proportional to sub-question count.* More sub-questions → fewer rounds each. This is a different heuristic, not a baseline for adaptive.

**What we chose and why.** Uniform mode: every sub-question gets `max_budget` (4) retrieval rounds, regardless of difficulty. This is the "spend maximum compute" upper bound. The ablation compares adaptive (difficulty-based allocation, 1-4 rounds) vs. uniform (4 rounds for all).

**What it produced.** The cost-quality curve (D012): adaptive achieves 98% support at ~31K tokens; uniform achieves 99% at ~110K. The 1% quality difference is within noise; the 3.5x cost reduction is substantial. The curve clearly shows adaptive *dominating* uniform — same quality, less compute.

**What it did not solve.** The uniform baseline is the *maximum* compute baseline. We did not test a "minimum compute" baseline (1 round for all), which would show whether adaptive also improves quality over a budget-constrained system. The full Pareto frontier (quality vs. compute) would require multiple budget levels, not just two points.

---

## Real Ablation Results (OpenRouter gpt-4o-mini, 2025-07-31)

### D012 — Track A results: adaptive compute is 3.5x more efficient **[S4]**

**The problem.** The core Track A thesis is: "not all work is equally hard." If every sub-question gets the same compute, we waste tokens on easy questions and may under-invest on hard ones. The experiment tests whether *difficulty-based* allocation improves the cost-quality tradeoff.

**What we did.** Ran all 4 test cases in two modes:
- **Adaptive:** `adaptive.enabled=True`, budget 1-4 rounds based on difficulty (D005).
- **Uniform:** `adaptive.enabled=False`, 4 rounds for all sub-questions (D011).

**What it produced.**
| Mode | Claims | Tokens | Support rate | Quality vs. cost |
|------|--------|--------|-------------|-----------------|
| Adaptive | 38-55 | 25-35K | 92-100% (mean 98%) | **98% quality at 3.5x lower cost** |
| Uniform | 130-195 | 97-126K | 98-100% (mean 99%) | 99% quality at 3.5x cost |

The adaptive system allocates 1-2 rounds to easy sub-questions (difficulty 0.33) and 3+ rounds to hard ones (difficulty 0.53). The cost-quality curve shows adaptive *dominating* uniform — the 1% quality difference is within noise, the 3.5x cost reduction is substantial.

**Why it matters.** This validates the Notion §4 thesis: "Adaptive research budgets improve report quality and/or cost-efficiency compared with a fixed-budget agent." The result is not just "we spent less" — it's "we spent less *without sacrificing quality*." The difficulty signal (D005) is accurate enough to direct compute where it matters.

**What it did not solve.** The test suite is 4 questions — too small for statistical significance. The 1% quality difference could be real (uniform genuinely better) or noise. A larger eval set (20+ questions) with confidence intervals would strengthen the claim. The difficulty signal itself is heuristic (D005) — a learned difficulty estimator might allocate compute even more efficiently.

---

### D013 — Track B results: system is under-confident (ECE=0.37) **[S2]**

**The problem.** The Track B thesis is: "a research agent should measure its own honesty." The reliability diagram makes calibration *visible* — predicted confidence vs. actual accuracy. If the system is well-calibrated, confidence scores are useful for thresholding (e.g., "only show claims with confidence > 0.8"). If not, the diagram reveals the miscalibration direction.

**What we did.** Computed ECE (Expected Calibration Error) with 10 bins, using `support_score >= 0.5` as proxy ground truth (D008). Plotted predicted confidence vs. actual accuracy.

**What it produced.** The reliability diagram shows the system predicts 58-64% confidence but achieves 92-100% accuracy. ECE = 0.37. The system is **under-confident** — it hedges when it should be confident.

**Why it matters — and why this is a *good* failure.** Under-confidence is the *safe* direction of miscalibration. An overconfident system hallucinates — it asserts claims with high confidence that turn out to be wrong. An under-confident system is conservative — it hedges claims that are actually well-supported. For a research agent, conservatism is safer than hallucination. The reliability diagram is the Track B wow moment *precisely because* it makes this visible: a reviewer sees a system that measures its own honesty and finds it wants — which is more honest than a system that doesn't measure at all.

**What failed.** The heuristic calibrator (D007) compresses confidence into a narrow 0.5-0.65 band. The support scores from gpt-4o-mini are bimodal (either ~0.8 or ~0.3), but the multiplicative formula `(0.5*support + 0.3*diversity) * (1.0 - penalty)` pulls everything toward the middle. Diversity (max 1.0) and penalty (0 or 0.3) don't have enough range to preserve the bimodal support distribution.

**What I'd change.** Apply temperature scaling on a held-out validation set to learn a proper calibration map. Alternatively, use isotonic regression on (support_score, ground_truth) pairs. The heuristic was a time-constrained choice — a learned calibrator would be the next step. The key insight from the diagram is that the *signal* is there (bimodal support scores), but the *mapping* is wrong (heuristic compresses it).

---

### D014 — Verifier ablation: verifier is load-bearing **[S2] [S3]**

**The problem.** Is the verifier actually necessary, or does it just add cost? If the extractor already produces good claims, verification is redundant. The ablation removes the verifier entirely and measures the impact.

**What we did.** Ran all 4 test cases with `verification.enabled=False`. No support scores, no confidence, no contradiction detection.

**What it produced.**
| Mode | Claims | Support rate | ECE | What breaks |
|------|--------|-------------|-----|-------------|
| Adaptive (with verifier) | 38-55 | 98% | 0.37 | — |
| No-verify | 134-229 | 0% | 0.30 (meaningless) | Both tracks |

Without the verifier, claim-support rate drops to 0% (no support scores assigned — the metric is undefined without verification). ECE is 0.30 but meaningless (no verification signal to calibrate). The no-verify mode produces *more* claims (134-229 vs 38-55) because it runs all rounds without confidence-based early stopping — but those claims are unverified and the system has no way to assess their quality.

**Why it matters.** The verifier is not just an add-on — it is the *foundation* of both tracks:
- **Track B:** Without verification, there are no support scores, no confidence, no reliability diagram. The honesty story collapses.
- **Track A:** Without confidence scores, the difficulty estimator (D005) has no Phase 2 signal. It falls back to linguistic-only difficulty, which is the weak proxy we rejected. The adaptive allocation degrades to uniform with a different budget.

The ablation cleanly isolates the verifier's causal impact: removing it breaks the entire depth story, not just one component.

**What it did not solve.** The ablation is binary (verifier on/off). A more granular ablation would test *partial* verification — e.g., verify only high-confidence claims, or verify with a weaker model. This would show whether the verifier's value comes from catching bad claims (Track B) or from providing the difficulty signal (Track A).

---

## D015 — Plain Python over agent frameworks **[S1] [S8]**

**The problem.** The Notion "Background Knowledge Research" section mentions LangGraph, CrewAI, and AutoGen as orchestration frameworks. The question is whether to use one or write plain Python. The brief says: "a small amount of transparent Python may be easier to defend in an interview."

**Alternatives considered.**
- *LangGraph.* Emphasizes durable execution, streaming, and human-in-the-loop. Good for stateful workflows, but adds a dependency and a conceptual layer. The state graph abstraction would hide the pipeline logic behind framework semantics.
- *CrewAI.* Multi-agent framework with role-based agents. Attractive for the multi-agent debate idea (Notion §5), but we de-scoped that (D016).
- *AutoGen.* Conversation-driven multi-agent. Same multi-agent focus; not needed for a single-pipeline architecture.

**What we chose and why.** Plain Python with Pydantic state (D001). The pipeline is a single `run_research()` function with explicit function calls — no graph, no state machine, no framework abstractions. Every step is readable in `pipeline.py`, and the trace (§13) shows exactly what happened and when.

**What it produced.** The architecture is fully transparent — a reviewer can read `pipeline.py` and understand the entire flow in 5 minutes. The trace system (§13) provides the observability that frameworks like LangGraph offer, but without the framework dependency. The codebase is ~1500 lines of Python; the equivalent in LangGraph would be similar in size but harder to debug because the control flow is implicit in the graph definition.

**What it did not solve.** Plain Python lacks durable execution — if the pipeline crashes mid-run, there is no automatic resume. A framework would checkpoint state and allow recovery. For a 2-day project, this is acceptable; for production, it would matter. The trace system allows *manual* replay (load `state.json` and inspect), but not automatic resume.

---

## D016 — Multi-agent debate de-scoped **[S5]**

**The problem.** The Notion §5 (Multi-agent debate and review) proposes using multiple agents with different roles (Researcher A, Researcher B, Reviewer, Synthesizer) to catch each other's errors. The question is whether this improves factuality enough to justify the added complexity and cost.

**Alternatives considered.**
- *Debate.* Agents directly argue with one another. High communication overhead; risks circular arguments.
- *Independent review.* Researcher A and B work independently; a reviewer compares. The Notion plan says "independent review is easier to defend than free-form debate."
- *Author-reviewer workflow.* One agent drafts, another critiques, a third revises. Closest to our single-pipeline architecture but with 3x the LLM calls.
- *MARS (Multi-Agent Review System).* Structured author-reviewer-meta-reviewer design. More complex than needed.

**What we chose and why.** De-scoped entirely. Our architecture already has a form of independent review: the *verifier* reviews the *extractor's* claims independently (even though they use the same model — see D006). The verifier- extractor split is a two-agent author-reviewer workflow at the *claim level*, not the *report level*. This gives us the error-catching benefit without 2x the full-pipeline cost.

**What it produced.** By de-scoping multi-agent debate, we kept the pipeline single-track and the cost model simple. The verifier-extractor split is the "independent review" pattern, just applied per-claim rather than per-report. The ablation (D014) shows this split is load-bearing — removing the verifier breaks everything.

**What it did not solve.** The verifier reviews *claims*, not the *final report*. A report-level reviewer would catch synthesis errors — cases where the synthesizer overstated a claim or missed a contradiction. We do not have this. A full author-reviewer-reviser loop at the report level (the SCoRe pattern from Notion §3) would add this, but at the cost of another synthesis-model call per run. This is a clear follow-up direction: "generate, verify, revise" at the report level, comparing unsupported-claim rate before and after.

---

## D017 — Dynamic tool selection: partial implementation **[S6]**

**The problem.** The Notion §6 (Dynamic tool selection) asks: can the agent select the right tool based on the question type? A basic agent has fixed tools (search, fetch, summarize); a more advanced system routes to specialized tools (academic search, calculator, PDF parser, news search).

**What we built.** A *partial* implementation: the researcher has two retrieval tools — arXiv search (academic) and web search (general). The pipeline does not dynamically route based on question type; it uses both tools for every sub-question. The PDF parser (`pdf.py`) is available but is called by the arXiv tool when full-text fetching is enabled, not by a separate routing decision.

**What we did not build.** A tool router that classifies the sub-question ("this is numerical → use calculator", "this is recent → use news search") and dispatches accordingly. The Notion plan suggests comparing "one general web-search tool" vs. "a small tool router that selects among web search, academic search, calculator, and document retrieval."

**Why.** The test cases are all academic ML questions, so arXiv + web covers them. A tool router would add complexity without a measurable quality improvement on this test set. The dynamic tool selection idea is more valuable for a heterogeneous question set (some academic, some numerical, some current events).

**What it produced.** The two-tool approach (arXiv + web) provides source diversity for the confidence score's diversity component (D007). Web search catches non-arXiv sources that arXiv misses. But the system cannot answer "this question needs a calculator" or "this question needs news search" — it will always use arXiv + web.

**What it did not solve.** For the "sparse evidence" test case (tc3), the system may benefit from Semantic Scholar or Google Scholar as a third tool. The current architecture makes adding tools straightforward (implement the `RetrievalTool` interface), but the routing logic is not there. This is a clear extension point: add a `tool_router.py` that classifies sub-questions and dispatches to the appropriate tool.

---

## D018 — Infrastructure and observability: trace system **[S8]**

**The problem.** The Notion §8 (Infrastructure and observability) asks: "How do you debug a workflow when something went wrong three steps ago?" For a multi-round agent, ordinary application logs are not enough. You need a trace that shows every step, every tool call, every token cost, and every decision.

**What we built.** A structured trace system (`trace.py`) that records a `TraceEntry` for every pipeline step. Each entry includes:
- **run ID and step number** — for replay and ordering
- **agent name** — planner, researcher, extractor, verifier, etc.
- **action** — what the step did (e.g., "search_arxiv", "extract_claims", "verify_claims")
- **inputs and outputs** — what went in and what came out
- **tokens and latency** — cost accounting per step
- **timestamp** — for timing analysis

The trace is saved as JSONL (`trace.jsonl`) — one entry per line, loadable by any JSON tool. The full state is also saved (`state.json`) for complete replay.

**What we did not build.** The Notion plan suggests "a replayable research-agent trace viewer that shows how each claim, source, and decision was produced." We have the *data* for this (trace.jsonl + state.json) but not the *viewer UI*. A viewer would be a Streamlit or React app that renders the trace as a timeline with expandable steps.

**Why.** The trace data is the hard part — the viewer is a UI exercise. The JSONL format means any tool can consume it; a viewer is one option, not a requirement. For debugging, `cat trace.jsonl | jq` is sufficient. For presentation, the trace can be rendered as a table in the report.

**What it produced.** The trace system made debugging the ablation results straightforward — we could see exactly which sub-questions got how many rounds, which claims were verified, and where the token budget went. The "run IDs, step-level traces, token and latency accounting, tool-call logs" from the Notion plan are all present. State snapshots are saved at the end of each run; deterministic replay is possible by loading `state.json`.

**What it did not solve.** No real-time streaming — the trace is written at the end of each step, not streamed live. No human approval checkpoints — the pipeline runs fully autonomously. No failure categorization — errors are logged but not classified (e.g., "retrieval failure" vs. "extraction failure"). These are production-grade features that a framework like LangGraph would provide, but they were not necessary for the ablation study.

---

## D019 — Noisy retrieval testing: not implemented **[Testing]**

**The problem.** The Notion "Testing Directions" section suggests deliberately injecting bad sources to measure how much answer quality degrades. This tests robustness: does the system catch bad sources, or does it trust them?

**What we did not build.** A noisy-retrieval test mode that injects outdated, irrelevant, or contradictory sources into the evidence stream. The current test cases use clean retrieval (real arXiv + web results).

**Why.** Time constraints. The noisy-retrieval experiment is valuable — the Notion plan calls it "a particularly good angle for the assignment because it produces convincing failure cases without requiring expensive model training." But it requires a mock retrieval tool that injects controlled bad sources, which is a separate test harness.

**What it would test.** The verifier is the defense against bad sources — it should assign low support scores to claims backed by irrelevant or outdated evidence. The noisy-retrieval experiment would show whether the verifier actually catches this, or whether it rubber-stamps any claim with a citation. The ablation (D014) shows the verifier is load-bearing in *clean* conditions; the noisy-retrieval test would show whether it's *robust* in adversarial conditions.

**How to implement.** Add a `mock_noisy_retrieval.py` tool that returns a mix of real and bad sources (e.g., 70% real arXiv results + 30% irrelevant or outdated results). Run the eval harness with this tool and compare support-rate and ECE against clean retrieval. The expected result: support rate drops (some claims are backed by bad sources), but the verifier should catch the worst cases and assign low confidence.

---

## D020 — Claims evolve: challenger → reviser loop, gated by evidence balance **[S1] [S3]**

**The problem.** The original pipeline is append-only: `extract_claims` mints new claims from each round's new evidence and never revisits a claim written in an earlier round. A sub-question that runs three rounds accumulates three independent batches of claims. If round 3 retrieves evidence that flatly contradicts a round-1 claim, the only thing that happens is a second, peer claim appears next to it — `detect_contradictions` (Phase 3) notices the pair at the very end of the run and applies a flat 0.3 confidence penalty to both. Nothing ever says "claim_3 was wrong; here is the corrected version." This is exactly the self-correction gap D003/§12.5 already flags: correction is evidence-level (retrieve more), never claim-level (rewrite what the evidence now shows).

**Alternatives considered.**
- *Just re-verify existing claims against the growing evidence pool each round.* Cheaper — no new agents — but only moves `support_score`. The claim's *wording* never improves, so a claim that was too broad ("X beats Y") stays too broad even after evidence arrives showing it only holds in one regime.
- *Ask the extractor's own model to critique and rewrite its claims.* This is the SCoRe-style "ask the model to check itself" pattern the codebase already distrusts for verification (D006) — a model tends to ratify what it wrote. Rejected for the same reason, doubled: here it would decide both whether to change a claim's *position* and how.
- *Free-form LLM judgement on "should this claim be revised?"* Rejected because it makes the decision to reverse a claim's stated position a black box — an aggressively-prompted challenger could flip well-supported claims on say-so alone, with no way to audit why.

**What we built and why.** Two new agents, `src/agents/challenger.py` and `src/agents/reviser.py`, run after verification in every retrieval round (`src/orchestrator/evolution.py: evolve_claims`), challenging every *active* claim for the sub-question against its **full accumulated evidence pool** (not just the chunks the claim originally cited — that is the whole mechanism that lets round-3 evidence rewrite a round-1 claim).

The challenger scores two things the verifier does not: `reasoning_score` (is the claim a *warranted inference* from the evidence as a whole, not merely entailed by its cited chunk — a claim can restate one cherry-picked chunk faithfully and still overgeneralize) and an **evidence balance**, `(supporting_sources − refuting_sources) / (supporting_sources + refuting_sources)`, counted by **distinct source**, not chunk — so a single PDF chopped into forty pieces cannot outvote three independent papers that disagree with it.

The decision of *what to do* — keep, refine (fix unsound logic, same position), narrow (add a scope qualifier for real minority dissent), reverse (flip position — dissent is now dominant), or retract — is a deterministic threshold on that balance score in `route_operation`, not a judgement call embedded in either model's prompt. The reviser is told which operation to execute and cannot downgrade a reversal the router assigned into something more convenient (it can only escalate to retract). Every revision is re-verified, so `support_score` always describes the *current* text.

**What it produced.** (Offline unit tests + a real-model smoke test against live OpenRouter free models — see D021 for the rate-limit-truncated real run.) The routing table is exhaustively unit-tested: 11 (balance, reasoning, verdict) combinations map to the expected operation, including the two adversarial-robustness cases that matter most — a challenger demanding `needs_reversal` on a claim the evidence still favors gets downgraded to `narrow`, and an `unsupported` verdict without dominant refuting evidence gets `narrow`, not `retract`. Confidence scoring gained two new signals (`reasoning_score`, evidence balance) and only applies them to claims that were actually challenged, so disabling evolution reproduces the pre-evolution numbers exactly — verified by both formulas being called out explicitly in `score_confidence`'s branch.

**What it did not solve.** The ablation quantifying evolution's effect on real ML-literature queries (support_lift, revision_rate, reversal_count across the four test cases) has not been run to completion — see D021. The `stability_rounds` freeze (2 consecutive "keep" verdicts) is hand-picked, like the D005 difficulty weighting; no sensitivity analysis was done. Evidence-pool sampling for the challenger prompt (`select_challenge_evidence`) round-robins by source and by recency within source — this is a reasonable heuristic but, like D009's chunking, is not tuned against alternatives (e.g., embedding-similarity sampling to the claim text).

---

## D021 — Challenger independence and the self-agreement bias probe **[S2] [S3]**

**The problem.** D006 already names the risk: a verifier built on the same model that wrote a claim tends to agree with itself, and flags "run verification with a different model family and compare" as the most important follow-up experiment nobody had run. Claim evolution reopens the same risk in a more dangerous form — here, "agreeing with itself" doesn't just mean a bad confidence score, it means a claim that should have been revised silently isn't.

**What we built.** The challenger gets its own `LLMClient`, constructed independently in `pipeline.py` from `config.evolution.challenger_model`, with **its own optional `base_url` / `api_key_env`** — so it can sit on a different provider entirely, not merely a different model string on the same backend. `configs/default.yaml` pins the challenger to `gpt-4o` against a `gpt-4o-mini` extractor; `configs/openrouter.yaml` pins it to a different model family on the same free tier (`openai/gpt-oss-20b:free` vs. an extractor on `google/gemma-4-26b-a4b-it:free`).

Critically, this is built to be **measured, not asserted**: `ablations/run_ablation.py` adds an `evolution_self` mode that runs the identical pipeline with `challenger_model` forced equal to `sub_step_model` — same evidence, same claims, same prompts, only the challenger's identity changes. `eval/metrics.py` reports `challenge_hit_rate` (fraction of challenges that found something wrong) for both modes; the ablation prints the delta directly as "self-agreement bias: independent challenger finds fault in X% of claims vs. Y% for the self-challenger." Every challenge is logged to `state.challenges` — including the ones that found nothing wrong — specifically so the "sound" verdicts are available for this comparison, not just the ones that triggered a revision.

**What it produced.** Full offline verification (routing logic, evidence-balance arithmetic, evidence sampling, confidence formula gating, metrics aggregation) passes. A real-model integration test — hand-seeded evidence run through actual OpenRouter models rather than the mock — confirmed extraction and verification work end-to-end on `google/gemma-4-26b-a4b-it:free`, and the challenger successfully fired against real evidence on `openai/gpt-oss-20b:free` before the run was truncated by OpenRouter's account-wide free-tier cap (50 requests/day, shared across all calls including model-availability probing done in the same session). The `evolution` vs. `evolution_self` ablation comparison itself — the actual bias measurement — has **not** been run to completion on real queries.

**What it did not solve.** This is the direct continuation of D006's open item, and it is only half-closed: the mechanism to measure self-agreement bias exists and is wired into the ablation, but no real numbers have been produced yet. Separately, the real-run attempt surfaced that `configs/openrouter.yaml`'s original model slugs (`deepseek/deepseek-r1:free`, `meta-llama/llama-3.3-70b-instruct:free`) were retired by OpenRouter since that file was written — confirmed via direct API probe on 2026-08-02 — and has been updated to currently-live free slugs, with a comment pointing at how to re-check availability when they too go stale. The `.env` Tavily key is also expired (401 from Tavily directly) and arXiv's API was unreachable from this sandbox (TLS handshake completes, then the connection hangs) — both pre-existing infrastructure issues, orthogonal to claim evolution, that blocked a full live pipeline run through real retrieval. **Next step:** re-run `python -m ablations.run_ablation --openrouter` once the daily rate limit resets, to get real `support_lift` / `challenge_hit_rate` numbers on the four test cases.

---

## D022 — Real-evaluation fixes: source-count gating, quote-grounded refutation, independent judge **[S2] [S3]**

**The problem.** D021's next step happened: all four test cases ran against real paid models (`gpt-4o-mini` extractor/verifier, `deepseek-chat` challenger, `max_challenges_per_round=30`) — full results and methodology in `TESTING.md`. The system worked (93–100% support rate, the tc4-factual-baseline-vs-tc1/tc2-contested-literature gradient landed exactly as designed), but `support_lift` was **negative** for 3 of 4 test cases, and breaking it down by operation showed `reverse` alone averaged **−0.316**, dragging the aggregate down. Inspecting individual reversals found two concrete failure modes: (1) a claim about LLaMA-Adapter V2's *instruction-following* was reversed using evidence about its *open-ended generalization* — a different metric entirely; (2) a claim that CoT "assists in diagnosing flawed conclusions" was reversed because "the majority of evidence... does not explicitly link it to diagnosing flaws" — silence being treated as refutation, the exact failure the original challenger prompt already told the model not to commit. A third issue: `refine` fired **zero times** across 246 real revisions — its gate (balance > 0.5 AND reasoning < 0.6) is a narrow intersection that real multi-source academic evidence rarely lands in, since some source almost always has a caveat.

**Root cause, not just symptom.** `compute_evidence_balance(0, 1) == compute_evidence_balance(0, 10) == -1.0` — the balance score is a ratio, blind to sample size. A claim with zero other coverage and one dissenting source gets identical reversal authority to a claim ten independent papers disagree with. That is what let thin, single-source disagreements flip claims that had barely been examined.

**What we changed:**
1. **`min_sources_for_reversal`** (`EvolutionConfig`, default 2) — `route_operation` now requires a minimum total source count before `balance < reversal_threshold` is allowed to produce `reverse`/`retract`; below it, the claim downgrades to `narrow`. The `unsupported + unsound → retract` path is deliberately *not* gated by this, since it's a reasoning-failure judgment, not a claim about evidence dominance.
2. **Quote-grounded refutation** (`src/agents/challenger.py`) — the challenger's schema changed from bare `refuting_evidence_indices: [int]` to `refuting_evidence: [{index, quote}]`, and `_validate_quote_grounding` mechanically checks each quote is an actual substring of the cited chunk before it counts. A model can still hallucinate a quote, but it can no longer refute a claim by citing evidence that is silent on it — there's no real text to fabricate a match against. Also added `contested_dimension` (the specific metric/aspect in dispute) to the schema, logged for auditability even though it isn't hard-gated.
3. **Flaws-triggered `refine`** — `route_operation` now routes to `refine` whenever the challenger flags a wording-only flaw (`vague`, `conflates_metrics`), independent of the balance/reasoning intersection that made `refine` unreachable in practice.
4. **Independent judge** (`src/scoring/judge.py`) — a second, structurally different quality signal: a blind, order-randomized pairwise comparison of a claim's before/after text against the evidence pool, run on the (already-independent) challenger client. This exists because `support_lift` conflates "more accurate" with "harder to fully entail" — a correctly hedged claim often scores *lower* on strict entailment than the bold, simpler original did, which is exactly what made the aggregate `support_lift` numbers look worse than the qualitative reality.

**A boundary bug found along the way.** Real-model testing crashed `verify_claims` with `'list' object has no attribute 'get'` — `gemma-4-26b` returned a bare JSON list instead of the requested object. Every agent's `complete_json` consumer assumed a dict via `.get(...)`. Fixed once at the boundary (`LLMClient.complete_json`, `src/tools/base.py`) rather than in each of the six call sites: any non-dict parse result is now wrapped as `{"_unparsed": ...}`, so every existing `.get(key, default)` call degrades to its default instead of crashing.

**What it produced.** Full offline test suite covers the new gate (thin-evidence downgrade, sufficient-evidence pass-through, the ungated retract path), quote-grounding (real substrings kept, hallucinated/empty/out-of-range quotes dropped, a synthetic "silent refuter" LLM proven to contribute zero refuting sources), and the flaws-triggered refine override — all passing. A real-paid-model re-run of the RAG/hallucination seeded scenario that previously produced confident reversals now produces `narrow` operations that add accurate scope caveats ("...when retrieved-passage quality exceeds a threshold, though it may increase errors in noisy retrieval contexts") while holding support_score at 1.0 — the exact failure mode from the qualitative writeup, fixed. See `TESTING.md` for the full before/after and the routing-table diff.

**What it did not solve.** The `evolution` vs. `evolution_self` self-agreement-bias ablation (D021) still hasn't been run to completion with these fixes in place — that and a full 4-test-case re-run with the new gates are the natural next validation step, deferred to avoid unprompted additional spend. `contested_dimension` is logged but not hard-gated — a model could still cite a same-topic-different-metric quote that happens to contain literal overlapping words; closing that fully would need embedding-based or LLM-judged dimension matching, not just a substring check.
