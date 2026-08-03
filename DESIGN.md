# DESIGN.md — the system I would build in production

> **Scope.** This is the *full design* the brief asks for in §2.1 — the system I would actually build,
> not the subset I built in two days. Where the two differ, the boundary is marked explicitly
> (§9), because the prioritisation is itself part of what's being evaluated.
>
> **Companion docs.** `ARCHITECTURE.md` walks the MVP as-built, file by file.
> `DECISIONS.md` is the decision log — 34 entries, each ending with *what it did not solve*.
> `TESTING.md` is the chronological record of what was measured and what broke.
> This document is the one that says *why the system is shaped the way it is*.

---

## 1. The problem, stated precisely

A deep research agent takes a vague question and returns a report a human will act on. The obvious
framing is a retrieval problem: find the right documents, summarise them well. **I think that framing
is wrong, and the design follows from rejecting it.**

Retrieval is largely solved for this use case — arXiv and a web index will surface relevant material
for almost any research question. The hard part is what happens *after*: a research process
accumulates evidence over time, and evidence arriving at minute nine can invalidate a conclusion
drawn at minute two. A system that never revisits its own conclusions will confidently report the
first plausible answer it found.

**The design commitment: claims are first-class, mutable objects with a revision history — not
immutable outputs of an extraction step.** Everything else in this document follows from that.

### 1.1 Assumptions

Stated up front because they bound everything downstream.

| # | Assumption | If it's false |
|---|---|---|
| A1 | No ground truth exists for a research report. Quality must be measured by proxy. | The whole evaluation stack (§6) collapses into "just check the answer." |
| A2 | Sources genuinely conflict. Disagreement is signal, not noise to average away. | Contradiction handling and the `contested` state are wasted machinery. |
| A3 | Users act on the output, so a confidently-wrong claim costs more than a hedged-correct one. | Optimise for coverage instead of calibration; the whole verification track shrinks. |
| A4 | LLM cost and latency are the binding constraints, not retrieval. | Adaptive allocation matters far less; retrieve everything and read it all. |
| A5 | Retrieved content is untrusted input. | §7.1 (prompt injection) is unnecessary. |
| A6 | The model will not reliably follow instructions that must *always* hold. | Half of §6.3 is unnecessary ceremony. **Measured three times — see §8.1.** |

---

## 2. Architecture & orchestration

> *Brief's dimension: "How is work structured and coordinated? What talks to what, and why?"*

### 2.1 Agent boundaries follow objectives, not tasks

The design principle: **a boundary exists where the optimisation target differs**, not where the
work is merely different. Splitting by task ("a searching agent, a writing agent") produces agents
that share an objective and duplicate each other's failures.

| Agent | Optimises for | Fails by |
|---|---|---|
| Planner | decomposition coverage | sub-questions that overlap or miss the intent |
| Researcher | evidence breadth | retrieving volume instead of diversity |
| Extractor | claim coverage | over-extraction; claims not traceable to text |
| Verifier | **entailment** — does the cited text support this? | rubber-stamping a faithful restatement of a cherry-picked chunk |
| **Challenger** | **warrant** — is this sound given *all* evidence? | fabricating refutations; over-objecting |
| Reviser | faithful execution of one operation | rewriting toward convenience |
| Judge | comparative quality | position bias (**measured: 30% of verdicts**) |
| Report critic | did we answer the question asked? | style critique instead of substance |
| Synthesiser | faithful assembly | overstating claim confidence in prose |

The verifier/challenger split is the sharp one and the least obvious. A claim can restate one chunk
faithfully — `support_score = 1.0` — and still be an unsound overgeneralization from the pool as a
whole. `support_score` cannot express that. `reasoning_score` is a genuinely separate axis, and
having both is what makes the routing in §2.3 possible.

### 2.2 What agents share, and deliberately do not

- **Shared:** the `ResearchState` object, mediated by the orchestrator. No agent reads another
  agent's prompt or output directly.
- **Not shared:** sub-questions are hermetic. If sq₁ retrieves a paper relevant to sq₃, sq₃
  re-retrieves it. This is deliberate — independence keeps them parallelisable and prevents one
  sub-question's framing contaminating another's — and it is genuinely wasteful. In production I'd
  add a shared evidence cache keyed by content hash, retaining the *reasoning* independence while
  dropping the redundant fetches.
- **Never shared:** the challenger does not see the extractor's reasoning, only its output. Giving
  it the extractor's justification would invite agreement with the reasoning rather than assessment
  of the claim.

### 2.3 The control flow, and the one rule that matters

```
Query → Plan → ⟨per sub-question⟩ → cross-source contradictions → synthesise → report critique
                    ↓ ↑
         retrieve → extract → verify → CHALLENGE → route → revise → re-verify
```

Two loops, and they are the only two places the system may change its mind: within a sub-question
(retrieval rounds), and over the finished report (Phase 5).

**The rule: changing a claim's *position* requires evidence dominance, not a critic's say-so.**
Routing is arithmetic over a source-weighted evidence balance:

```
balance = (supporting_sources − refuting_sources) / (supporting_sources + refuting_sources)
```

Counted by **distinct source, not chunk** — one paper split into forty chunks must not outvote three
papers that disagree with it. Thresholds on that number decide `keep | refine | narrow | reverse |
retract`. An aggressive critic cannot flip a well-supported claim by being loud, the reason a claim
reversed is *a number in the trace*, and the system's aggressiveness is a config knob rather than a
prompt rewrite.

**Measured caveat (D030):** sweeping all four routing thresholds over 715 stored challenges,
**two of them change zero decisions across their entire plausible range.** Behaviour is set by
`reversal_balance_threshold` and `min_sources_for_reversal` alone. In production I would delete the
inert knobs rather than leave configuration that implies tuning capability it doesn't have.

### 2.4 Orchestration in production

The MVP runs everything serially in one process. That was the right MVP call and the wrong
production design.

| Concern | Production design |
|---|---|
| **Parallelism** | Sub-questions are independent by construction — fan out. The 12-hour and 2.3-hour eval runs were serial execution of embarrassingly parallel work. |
| **Scheduling** | A global budget pool with work items ranked by marginal value (built as `scheduler.py`), not per-item thresholds. Cost becomes a knob rather than an emergent property. |
| **Durability** | Checkpoint `ResearchState` after each round; resume from the last checkpoint. A 2-hour run that dies at 90% currently starts over. |
| **Backpressure** | Retry with exponential backoff and a provider-level circuit breaker. Rate limits crashed two full runs during evaluation. |
| **Idempotency** | Content-hash cache on retrieval and on `(claim, evidence, prompt_version)` for LLM calls. Most evaluation cost was re-deriving identical results. |

I deliberately did **not** use LangGraph/CrewAI. ~1,500 lines of plain Python keeps `pipeline.py`
readable end-to-end, which mattered more than durable execution for a system whose *entire point*
is auditability. In production the calculus flips once resume and fan-out are needed, and a
framework becomes worth its opacity.

---

## 3. Memory & context

> *Brief's dimension: "How does the system manage state, context limits, and decide what to keep vs. discard?"*

**State lives in typed objects, not in a context window.** `ResearchState` is Pydantic throughout;
each agent receives only its own inputs and the orchestrator decides what is visible. No agent ever
sees full pipeline state. Zero context-overflow errors across every run in this project — not
because contexts are large, but because nothing accumulates in one.

Three distinct compaction problems, with different answers:

**1. Evidence into a challenge prompt.** A real pool runs to hundreds of chunks. Naive truncation
degenerates the balance score: forty consecutive chunks of one paper make every index resolve to the
same source, and the claim reads as unanimously refuted. So selection **round-robins across sources**,
prioritising the claim's own citations, then breadth, then recency. The metric determines the
sampling strategy — that coupling is the design.

**2. History into a reformulation prompt.** The query reformulator never sees the evidence pool,
only a digest of `retrieval_attempts`: queries already tried, source titles returned, chunk counts,
plus the weakest standing claims. A structured summary of "what we did and what it got us" is
cheaper *and* more actionable than the text it summarises.

**3. A claim's own history.** Each claim carries `text_history` fingerprints and a full
`revisions` log. This is what makes oscillation detectable (§6.4) and the revision history is,
I'd argue, **the system's most valuable output** — more than the report.

In production I'd add a fourth: **cross-run memory.** Nothing currently persists between runs, so
the system cannot learn that a given source is consistently low-quality, or that a query pattern
reliably fails. That's the largest missing capability in this section.

---

## 4. Tool design & failure

> *Brief's dimension: "What tools does the system have, and what happens when they fail?"*

arXiv primary (no key, structured metadata, PDF-first with abstract fallback); web secondary through
a fallback chain (Tavily → DuckDuckGo → Wikipedia). Every tool degrades to empty rather than raising.

**The real lesson here is not the tool list — it's the failure taxonomy.** Three production failures
appeared only under real load, none reproducible with a mock:

1. A model returned a bare JSON **list** where an object was requested → `'list' object has no attribute 'get'`
2. A provider returned **HTTP 200 with `choices: None`** — a well-formed envelope containing nothing
3. Rate limits (429) crashed two multi-hour runs outright

All three were fixed **once at the client boundary**, not at the six call sites: non-dict parses wrap
to `{"_unparsed": …}` so every `.get(k, default)` degrades to its default; a missing `choices` is
treated as an empty completion.

> **The generalisable finding:** a mock can simulate malformed *content* but not a malformed
> *envelope*. Any system that only tests against mocks has an untested failure class, and it is the
> class that takes production down.

Production additions: per-provider circuit breakers, a declared timeout/retry budget per tool, and
**failure classification in the trace** (retrieval-empty vs. parse-failure vs. rate-limit), which
currently isn't distinguished — so a run that produced nothing looks identical to one where the API
was down.

---

## 5. Quality & reliability

> *Brief's dimension: "How does the system stay honest when evidence is incomplete or unreliable?"*

Four mechanisms, in increasing order of how much they earn their keep.

**1. Per-claim verification.** Every claim scored against its cited evidence. Load-bearing:
removing it drops support assessment to zero *and* starves the difficulty signal.

**2. Cross-source contradiction detection**, restricted to claims from *different* sources —
within-source "contradictions" are usually extraction artifacts.

**3. Adversarial challenge with quote-grounded refutation.** The challenger must supply a
**verbatim quote, mechanically checked as a substring** of the cited chunk, before evidence counts as
refuting. This exists because of a measurement: **159 of 715 challenges (22.2%)** proposed refuting
citations that failed that check — roughly one in five was refuting on evidence that didn't say what
was claimed. The prompt already forbade it.

**4. Three honest terminal states, not two.** A claim ends as `supported`, `retracted`, or
`contested` — the third added after discovering that claims can oscillate indefinitely between
wordings (§6.4). Reporting whichever wording the last pass happened to produce would present a
coin-flip as a conclusion.

The output contract makes all of this visible: **every claim carries the confidence the system
computed, and retracted claims are disclosed rather than dropped.** That index is rendered
deterministically from state, *not* requested from the model — see §8.1.

---

## 6. Evaluation as a first-class component

Assumption A1 says no ground truth exists. That makes evaluation a **design problem**, not a
reporting afterthought, and in production it is a component with an owner.

### 6.1 A layered proxy stack, each layer with a stated failure mode

1. **Mechanical checks** — no LLM. The only layer that cannot be wrong about what it measures.
2. **Verifier `support_score`** — conflates "more accurate" with "harder to fully entail."
3. **Blind, order-randomised pairwise judge** — a structurally different question, so it cannot
   inherit that conflation.
4. **Paired ablations with proper statistics** — isolate one variable; report intervals.

**The circularity is real and must be stated:** verifier, challenger and judge are all LLMs, and the
judge reuses the challenger's client. No independent ground truth exists anywhere in the stack. The
only genuine fix is human adjudication on a sample, which production should budget for.

### 6.2 Design the clean experiment, and reject the easy one

The repo shipped an `evolution` vs `evolution_self` ablation — two full pipeline runs. **I rejected
it**: separate runs retrieve different evidence and extract different claims, confounding challenger
identity with different inputs. The replacement replays *identical* (claim, evidence-pool) pairs
through both challengers, making it a paired test with a McNemar exact statistic. Same question,
an order of magnitude more power, and a fraction of the cost.

### 6.3 Know when your metric cannot discriminate

**Three independent experiments produced the same non-answer** because support rate saturates at
98–100%: adaptive vs. uniform allocation, ranking vs. thresholding, and weak-plus-machinery vs.
strong-model-alone. All three were measuring a ceiling.

This is a production concern, not an academic one: **a metric that cannot separate your arms will
silently approve any change.** The design response is to check discriminative power *before*
running the comparison, and to maintain at least one test case where the metric has headroom.
The suite here was built to stress claim evolution and does not stress compute allocation — a
test-design failure that three experiments had to surface.

### 6.4 Measure the mechanism, not just the output

The most informative experiment in the project froze the evidence pool and re-challenged the same
claims five times. Result: **the loop never converges** — flat ~50% keep rate against evidence that
never changed, with 6 of 12 claims oscillating between wordings they'd already held.

That finding **invalidated a scheduler written hours earlier**, whose allocation signal counted
"claims changed" as productivity. Oscillating claims change every round forever.

> The design rule: **run the experiment that could invalidate a component before shipping it**,
> not after. The ordering is the difference between a known flaw and a hidden one.

---

## 7. The concerns the brief didn't name

> *"A production system has concerns we haven't mentioned — identifying and addressing the ones that
> matter most for this problem is part of the exercise."*

### 7.1 Retrieved content is untrusted input

The system feeds arbitrary web pages and PDFs **verbatim** into verifier and challenger prompts. For
a product whose entire value is trustworthy verification, an attacker who can flip a verdict by
publishing a web page defeats the product, not a component. This is the risk most specific to *this*
architecture.

**Measured**, rather than merely declared: 14 claims × 4 payload styles, paired against clean
controls. **1 successful verdict flip in 56 attempts (1.8%)**, and only from the payload imitating a
role boundary inside evidence text. Fabricated authority — an invented meta-analysis asserting the
claim — moved nothing, which suggests the verifier anchors on quoted evidence rather than claimed
authority.

Production design: delimit evidence with unforgeable markers, strip role tokens before insertion,
and treat any evidence chunk containing instruction-like text as suspect and quarantined. Note the
caveat honestly — 12 of 14 controls sat at the support floor, so this tested "can injection rescue a
hopeless claim," not "can it tip a borderline one."

### 7.2 Cost and latency are correctness concerns

Runs cost hours and dollars, and nothing enforced a ceiling: per-item thresholds meant total spend
was *emergent*. A production system needs a hard budget the scheduler spends against, per-run cost
attribution in the trace, and a graceful "stop and report what you have" path. The design answer is
the global pool in §2.4 — cost as a parameter, not an outcome.

### 7.3 Reproducibility

Mock runs used Python's builtin `hash()` on strings, which is salted per process — so "deterministic"
mock ablations silently weren't comparable across runs. Fixed with a stable digest. Production needs
seeded sampling, pinned prompt versions in the trace, and recorded model versions, because
**a result you cannot reproduce is not a result.**

### 7.4 Auditability for a system that changes its mind

Standard observability answers "what happened." A system that revises its own conclusions needs to
answer **"why did this claim change, and what did it used to say?"** Every revision records the
before/after text, the operation, the evidence balance that triggered it, and the support delta.
This is the difference between a system you can debug and one you can only restart — and it is the
component I'd protect first under time pressure.

### 7.5 Knowing which failures money can fix

Measured directly (§8.1): **capability improves judgement and does nothing for property compliance.**
That's a procurement rule, not a philosophical observation — it tells you when to buy a better model
and when to write code instead, which is the most consequential recurring decision in an LLM system.

---

## 8. What the measurements changed about the design

The design above is not what I started with. Five things changed because an experiment said so.

### 8.1 Properties belong in code; judgement is what you buy

| Told the model to… | Result |
|---|---|
| never treat silence as refutation | 22% ungrounded refutations anyway |
| emit a confidence marker on every claim | **zero** emitted |
| declare a stalemate rather than re-litigating | **0 of 60** |

Each only held once it moved into code. But the boundary is precise, not blanket: swapping the judge
from deepseek-chat to gemini-2.5-pro took self-consistency from **50% → 90%** and position-locked
verdicts from **33% → 0%**. And going the other way, a *stronger* challenger produced **more**
ungrounded refutations (4.4% → 13.6%).

> **Enforce properties in code. Spend money on judgement.**

### 8.2 A ratio is blind to sample size

`balance(0 supporting, 1 refuting)` and `balance(0, 10)` are both −1.0. One thin dissenting source
had the same reversal authority as ten papers. Fixed with a minimum-source gate — the kind of defect
that only surfaces when you inspect individual decisions rather than aggregates.

### 8.3 Adaptivity's value depends entirely on the baseline

Adaptive allocation is 3.5× cheaper than spending maximum everywhere, **and** 1.74× more expensive
than spending minimum everywhere, at indistinguishable quality. Both true. A headline number without
its baseline is not a result.

### 8.4 Convergence must be demonstrated, not assumed

`stability_rounds` looked like a convergence mechanism and is a **circuit breaker** — it stops
looking rather than settling. Oscillation is now treated as *diagnostic*: a claim that cannot settle
means the evidence does not determine the answer.

### 8.5 Automated conclusions need the same scrutiny as model output

Three experiment scripts printed verdict lines; **all three overstated their own data**, each from a
threshold written before seeing results with no "inconclusive" branch. A project whose thesis is
"don't claim more confidence than your evidence supports" built tooling that did exactly that.
Design rule: always emit the contingency table, never only the verdict.

---

## 9. MVP boundary

| Built and measured | Designed, not built |
|---|---|
| Claim evolution + arithmetic routing | Parallel sub-question execution |
| Quote-grounded refutation | Durable checkpointing / resume |
| Report self-correction (two tiers) | Content-hash caching |
| Query reformulation + compaction | Retry / backoff / circuit breakers |
| Live process narration | Cross-run memory |
| Deterministic confidence index | Learned calibration |
| Global scheduler (alternate strategy) | Embedding retrieval + reranking |
| Six ablations, 82 offline checks | Tool router; multi-modal output |
| Prompt-injection measurement | Prompt-injection *defence* |

**Why this split.** Everything in the left column either *is* the core idea or *tests* it. Everything
in the right column is engineering that makes the idea deployable but cannot tell you whether the
idea is correct. With two days, learning that reversal damages support — and fixing it — was worth
more than making the system resumable.

The one item I'd move left with another day: **a test case where the metric has headroom.** Three
experiments couldn't discriminate for lack of it, which makes it the highest-value missing piece
in the entire project.

---

## 10. Open questions I would want answered next

1. **Is non-convergence a defect or the correct behaviour?** If claims oscillate because sources
   genuinely conflict, the system is right and only the reporting was wrong.
2. **Does the adversarial framing cause the oscillation?** The challenger is told *"do not be
   agreeable"*; conceding a stalemate contradicts its assigned role. Testable by softening it.
3. **Would a trained PRM beat the prompted verifier?** The evolution traces are the dataset —
   570 revisions with before/after text, support scores, and blind judge verdicts.
4. **Does evolution help on questions where round-1 evidence is genuinely insufficient?** Unknown,
   because no such test case exists yet. This is question zero; the others are downstream of it.
