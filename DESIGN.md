# Design

The system I'd build for production, and why it's shaped this way. `ARCHITECTURE.md` covers what
was actually built, file by file; `DECISIONS.md` is the running decision log; `TESTING.md` records
what was measured and what broke. This document is the reasoning.

---

## 1. The problem

A deep research agent takes a vague question and returns a report someone will act on. The obvious
way to frame that is as a retrieval problem: find the right documents, summarise them well. I think
that framing is wrong, and most of the design follows from rejecting it.

Retrieval is largely solved here. arXiv and a web index will surface relevant material for almost
any research question you can pose. The hard part comes after. Research accumulates evidence over
time, and something you find nine minutes in can invalidate a conclusion you drew after two. A
system that never revisits its own conclusions will confidently report the first plausible answer
it found and stop.

So the central commitment is that claims are mutable objects with a revision history, rather than
immutable outputs of an extraction step. Everything below follows from that.

### 1.1 Assumptions

| # | Assumption | What breaks if it's wrong |
|---|---|---|
| A1 | No ground truth exists for a research report; quality has to be measured by proxy | The whole evaluation stack in §6 collapses into "just check the answer" |
| A2 | Sources genuinely conflict, and disagreement is signal | Contradiction handling and the `contested` state are wasted machinery |
| A3 | Users act on the output, so a confidently-wrong claim costs more than a hedged-correct one | Optimise for coverage instead of calibration, and the verification track shrinks a lot |
| A4 | LLM cost and latency bind before retrieval does | Adaptive allocation matters much less; just retrieve everything |
| A5 | Retrieved content is untrusted input | §7.1 is unnecessary |
| A6 | The model won't reliably follow instructions that must *always* hold | Half of §5 is ceremony. This one got measured three times (§8.1) |

---

## 2. Architecture and orchestration

### 2.1 Agent boundaries

A boundary belongs where the optimisation target changes, not where the work merely looks different.
Splitting by task — a searching agent, a writing agent — gives you agents that share an objective
and therefore duplicate each other's blind spots.

| Agent | Optimises for | Characteristic failure |
|---|---|---|
| Planner | decomposition coverage | sub-questions that overlap or miss the intent |
| Researcher | evidence breadth | retrieving volume instead of diversity |
| Extractor | claim coverage | over-extraction; claims not traceable to text |
| Verifier | entailment: does the cited text support this? | rubber-stamping a faithful restatement of a cherry-picked chunk |
| Challenger | warrant: is this sound given *all* the evidence? | fabricating refutations, or objecting to everything |
| Reviser | executing one operation faithfully | rewriting toward whatever is convenient |
| Judge | comparative quality | position bias — measured at 30% of verdicts |
| Report critic | did we answer what was asked? | critiquing style instead of substance |
| Synthesiser | faithful assembly | overstating claim confidence in prose |

The verifier/challenger split is the one worth defending. A claim can restate one chunk perfectly
and score 1.0 on support while still being an unsound generalisation from the pool as a whole.
`support_score` has no way to say that. `reasoning_score` is a separate axis, and having both is
what makes the routing in §2.3 possible at all.

### 2.2 What agents share

Agents share the `ResearchState` object, mediated by the orchestrator. None of them reads another
agent's prompt or raw output.

Sub-questions share nothing. If sq₁ retrieves a paper that's relevant to sq₃, sq₃ goes and fetches
it again. That keeps them independent and parallelisable and stops one sub-question's framing
leaking into another, and it's also plainly wasteful. In production I'd put a content-hash cache
under retrieval, which drops the redundant fetches while keeping the reasoning independent.

One thing is deliberately withheld: the challenger sees the extractor's claim but not its
reasoning. Show it the justification and you invite it to agree with the argument rather than
assess the claim.

### 2.3 Control flow

```
Query → Plan → ⟨per sub-question⟩ → cross-source contradictions → synthesise → report critique
                    ↓ ↑
         retrieve → extract → verify → CHALLENGE → route → revise → re-verify
```

There are two loops, and they're the only two places the system can change its mind: inside a
sub-question, and over the finished report.

The rule that matters is that changing a claim's *position* requires evidence dominance rather than
a critic's insistence. Routing is arithmetic over a source-weighted balance:

```
balance = (supporting_sources − refuting_sources) / (supporting_sources + refuting_sources)
```

Counting distinct sources rather than chunks is load-bearing. One paper chopped into forty chunks
shouldn't outvote three papers that disagree with it. Thresholds on that number pick between
`keep`, `refine`, `narrow`, `reverse` and `retract`. An aggressive critic can't flip a well-supported
claim by being loud, the reason any claim reversed is a number sitting in the trace, and how
aggressive the system is becomes a config value instead of a prompt rewrite.

Worth flagging: sweeping all four routing thresholds over 715 stored challenges, two of them change
zero decisions anywhere in their plausible range. Behaviour is set by `reversal_balance_threshold`
and `min_sources_for_reversal`. In production I'd delete the other two rather than ship configuration
that implies tuning capability it doesn't have.

### 2.4 Orchestration in production

The MVP runs serially in one process. Right call for an MVP, wrong design for production.

| Concern | What production needs |
|---|---|
| Parallelism | Sub-questions are independent by construction, so fan them out. The 12-hour and 2.3-hour eval runs were serial execution of embarrassingly parallel work. |
| Scheduling | A global budget pool with work ranked by marginal value (this exists as `scheduler.py`) rather than per-item thresholds, so cost is a knob instead of an outcome. |
| Durability | Checkpoint `ResearchState` each round and resume from it. Today a two-hour run that dies at 90% starts over. |
| Backpressure | Exponential backoff and a per-provider circuit breaker. Rate limits killed two full runs during evaluation. |
| Idempotency | Content-hash cache on retrieval, and on `(claim, evidence, prompt_version)` for LLM calls. A large share of evaluation cost was re-deriving identical results. |

I didn't use LangGraph or CrewAI. About 1,500 lines of plain Python keeps `pipeline.py` readable
start to finish, which mattered more than durable execution for a system whose whole point is
auditability. That calculus flips once you need resume and fan-out, and at that point a framework
earns its opacity.

---

## 3. Memory and context

State lives in typed objects rather than a context window. `ResearchState` is Pydantic throughout,
each agent gets only its own inputs, and the orchestrator decides what's visible. No agent ever sees
the full pipeline state. There were no context-overflow errors in any run, which isn't because the
contexts are large — it's because nothing accumulates in one place.

Three separate compaction problems, with different answers.

**Evidence going into a challenge prompt.** A real pool runs to hundreds of chunks. Naive truncation
wrecks the balance score: forty consecutive chunks from one paper mean every index the challenger
returns resolves to the same source, and the claim comes back looking unanimously refuted. Selection
round-robins across sources instead, taking the claim's own citations first, then breadth, then
recency. The metric dictates the sampling strategy, which is the part I'd keep.

**History going into a reformulation prompt.** The query reformulator never sees the evidence pool.
It gets a digest of `retrieval_attempts` — queries already tried, source titles that came back,
chunk counts — plus the weakest standing claims. A summary of what we did and what it got us is
cheaper than the underlying text and easier to act on.

**A claim's own history.** Each claim carries text fingerprints and a full revision log. That's what
makes oscillation detectable (§6.4), and I'd argue the revision history is the most valuable thing
the system produces, more so than the report.

There's a fourth I'd add in production: cross-run memory. Nothing persists between runs, so the
system can't learn that a source is consistently low quality or that a query pattern reliably
fails. That's the biggest gap in this section.

---

## 4. Tools and failure

arXiv is primary — no key needed, structured metadata, PDF first with an abstract fallback. Web
search is secondary through a fallback chain of Tavily, DuckDuckGo, Wikipedia. Every tool degrades
to empty rather than raising.

The interesting part isn't the tool list, it's the failure taxonomy. Three failures showed up only
under real load, and none of them were reproducible against a mock:

1. A model returned a bare JSON list where an object was requested, giving
   `'list' object has no attribute 'get'`
2. A provider returned HTTP 200 with `choices: None` — a well-formed envelope containing nothing
3. Rate limits killed two multi-hour runs outright

The first two were fixed once at the client boundary rather than at the six call sites. Non-dict
parses get wrapped as `{"_unparsed": …}` so every `.get(k, default)` falls through to its default,
and a missing `choices` is treated as an empty completion.

The generalisable version: a mock can produce malformed *content* but not a malformed *envelope*.
If you only test against mocks you have an untested failure class, and it's the one that takes
production down.

Production would add per-provider circuit breakers, a declared timeout and retry budget per tool,
and failure classification in the trace. Right now retrieval-empty, parse-failure and rate-limited
all look identical after the fact, so a run that found nothing is indistinguishable from a run where
the API was down.

---

## 5. Staying honest

Four mechanisms, roughly in order of how much they earn their place.

**Per-claim verification.** Every claim scored against its cited evidence. This is load-bearing:
remove it and support assessment goes to zero, and the difficulty signal starves along with it.

**Cross-source contradiction detection**, restricted to claims from different sources.
Within-source contradictions are usually extraction artifacts rather than real disagreement.

**Adversarial challenge with quote-grounded refutation.** Before evidence counts as refuting, the
challenger has to supply a verbatim quote that's mechanically checked as a substring of the cited
chunk. This exists because of a measurement: 159 of 715 challenges (22.2%) proposed refuting
citations that failed the check. Roughly one in five was refuting on evidence that didn't say what
was claimed, and the prompt already forbade exactly that.

**Three terminal states rather than two.** A claim ends `supported`, `retracted`, or `contested`.
The third was added after finding that claims can oscillate between wordings indefinitely (§6.4).
Reporting whichever wording the last pass happened to land on would dress a coin flip as a
conclusion.

The output contract makes this visible: every claim carries the confidence the system computed, and
retracted claims are disclosed rather than dropped. That index is rendered from state in code, not
requested from the model, for reasons in §8.1.

---

## 6. Evaluation as a component

A1 says there's no ground truth. That makes evaluation a design problem rather than a reporting
step, and in production it's a component with an owner.

### 6.1 A layered proxy stack

Each layer has a stated failure mode:

1. **Mechanical checks** — no LLM involved, and the only layer that can't be wrong about what it
   measures
2. **Verifier `support_score`** — conflates "more accurate" with "harder to fully entail"
3. **Blind, order-randomised pairwise judge** — asks a structurally different question, so it
   doesn't inherit that conflation
4. **Paired ablations with real statistics** — isolate one variable, report intervals

The circularity has to be said out loud: verifier, challenger and judge are all LLMs, and the judge
reuses the challenger's client. There is no independent ground truth anywhere in the stack. The only
real fix is human adjudication on a sample, and production should budget for it.

### 6.2 Design the clean experiment

The repo originally had an `evolution` vs `evolution_self` ablation built as two full pipeline runs.
I threw it out. Separate runs retrieve different evidence and extract different claims, so
challenger identity ends up confounded with different inputs. The replacement replays identical
(claim, evidence-pool) pairs through both challengers, which makes it a paired test with a McNemar
statistic — same question, roughly an order of magnitude more power, and a fraction of the cost.

### 6.3 Know when your metric can't discriminate

Three separate experiments produced the same non-answer because support rate saturates at 98–100%:
adaptive vs uniform allocation, ranking vs thresholding, and weak-model-plus-machinery vs
strong-model-alone. All three were measuring a ceiling.

This is a practical concern rather than an academic one. A metric that can't separate your arms will
silently approve any change you make. The response is to check discriminative power before running
the comparison, and to keep at least one test case where the metric has headroom. The suite here was
built to stress claim evolution and doesn't stress compute allocation, which is a test-design
failure that took three experiments to surface.

### 6.4 Measure the mechanism, not just the output

The most informative experiment in the project froze the evidence pool and re-challenged the same
claims five times over. The loop never converges: a flat ~50% keep rate against evidence that never
changed, with 6 of 12 claims oscillating between wordings they'd already held.

That result invalidated a scheduler I'd written hours earlier, whose allocation signal counted
"claims changed" as evidence of productivity. Oscillating claims change every round, forever.

The rule I'd take from it: run the experiment that could invalidate a component before shipping it.
The ordering is the whole difference between a known flaw and a hidden one.

---

## 7. Other production concerns

### 7.1 Retrieved content is untrusted input

The system feeds arbitrary web pages and PDFs verbatim into verifier and challenger prompts. For a
product whose value proposition is trustworthy verification, someone who can flip a verdict by
publishing a web page has defeated the product rather than a component. This is the risk most
specific to this particular architecture.

It's measured rather than just asserted: 14 claims against 4 payload styles, paired with clean
controls. One successful verdict flip in 56 attempts, or 1.8%, and only from the payload that
imitates a role boundary inside the evidence text. Fabricated authority — an invented meta-analysis
asserting the claim outright — moved nothing, which suggests the verifier anchors on quoted evidence
rather than on claims of authority.

Production would delimit evidence with unforgeable markers, strip role tokens before insertion, and
quarantine any chunk containing instruction-like text. The caveat is worth stating: 12 of the 14
controls sat at the support floor, so this tested whether injection can rescue a hopeless claim, not
whether it can tip a borderline one.

### 7.2 Cost and latency are correctness concerns

Runs cost hours and dollars and nothing enforced a ceiling, because per-item thresholds make total
spend an emergent property. Production needs a hard budget the scheduler spends against, per-run
cost attribution in the trace, and a graceful "stop and report what you have" path. The global pool
in §2.4 is the answer — cost as a parameter rather than an outcome.

### 7.3 Reproducibility

Mock runs used Python's builtin `hash()` on strings, which is salted per process, so "deterministic"
mock ablations quietly weren't comparable across runs. Fixed with a stable digest. Production needs
seeded sampling, prompt versions pinned in the trace, and recorded model versions, because a result
you can't reproduce isn't a result.

### 7.4 Auditability for a system that changes its mind

Ordinary observability tells you what happened. A system that revises its own conclusions has to
answer a harder question: why did this claim change, and what did it say before? Every revision
records the before and after text, the operation, the evidence balance that triggered it, and the
support delta. That's the difference between a system you can debug and one you can only restart,
and it's the component I'd protect first if time got short.

### 7.5 Knowing which failures money can fix

§8.1 has the measurement: capability improves judgement and does nothing for property compliance.
That's a procurement rule. It tells you when to buy a better model and when to write code instead,
which is the most frequent expensive decision in any LLM system.

---

## 8. What the measurements changed

The design above isn't what I started with. Five things changed because an experiment said so.

### 8.1 Properties belong in code; judgement is what you pay for

| Instruction given to the model | What happened |
|---|---|
| never treat silence as refutation | 22% ungrounded refutations anyway |
| emit a confidence marker on every claim | zero emitted |
| declare a stalemate instead of re-litigating | 0 of 60 |

Each of these only held once it moved into code. But the boundary is specific rather than blanket:
swapping the judge from deepseek-chat to gemini-2.5-pro moved self-consistency from 50% to 90% and
position-locked verdicts from 33% to 0%. In the other direction, a stronger challenger produced
*more* ungrounded refutations, 4.4% up to 13.6%.

So: enforce properties in code, spend money on judgement.

### 8.2 A ratio is blind to sample size

`balance(0 supporting, 1 refuting)` and `balance(0, 10)` are both −1.0, so one thin dissenting
source had the same reversal authority as ten papers. Fixed with a minimum-source gate. This is the
kind of defect you only find by reading individual decisions rather than aggregates.

### 8.3 Adaptivity's value depends on the baseline

Adaptive allocation is 3.5× cheaper than spending maximum everywhere, and 1.74× more expensive than
spending minimum everywhere, at quality you can't tell apart. Both numbers are real. A headline
figure without its baseline isn't a result.

### 8.4 Convergence has to be demonstrated

`stability_rounds` looked like a convergence mechanism and turned out to be a circuit breaker: it
stops looking rather than settling. Oscillation is now treated as diagnostic — a claim that can't
settle is telling you the evidence doesn't determine the answer.

### 8.5 Automated conclusions need the same scrutiny as model output

Three experiment scripts printed verdict lines and all three overstated their own data, each from a
threshold written before the results existed with no branch for "inconclusive". A project whose
thesis is "don't claim more confidence than your evidence supports" built tooling that did exactly
that. The fix is to always emit the contingency table and never just the verdict.

---

## 9. What was built vs what was designed

| Built and measured | Designed, not built |
|---|---|
| Claim evolution + arithmetic routing | Parallel sub-question execution |
| Quote-grounded refutation | Durable checkpointing and resume |
| Report self-correction (two tiers) | Content-hash caching |
| Query reformulation + compaction | Retry, backoff, circuit breakers |
| Live process narration | Cross-run memory |
| Deterministic confidence index | Learned calibration |
| Global scheduler (alternate strategy) | Embedding retrieval and reranking |
| Six ablations, 82 offline checks | Tool router; multi-modal output |
| Prompt-injection measurement | Prompt-injection defence |

Everything on the left either is the core idea or tests it. Everything on the right is engineering
that would make the idea deployable but can't tell you whether the idea is any good. Given two days,
finding out that reversal damages support and then fixing it was worth more than making the system
resumable.

The one item I'd move left given another day is a test case where the metric has headroom. Three
experiments failed to discriminate for want of it, which makes it the highest-value missing piece in
the project.

---

## 10. Open questions

1. **Is non-convergence a defect or correct behaviour?** If claims oscillate because the sources
   genuinely conflict, the system is right and only the reporting was wrong.
2. **Does the adversarial framing cause the oscillation?** The challenger is told "do not be
   agreeable", so conceding a stalemate contradicts the role it was given. Testable by softening it.
3. **Would a trained PRM beat the prompted verifier?** The evolution traces are the dataset: 570
   revisions with before and after text, support scores, and blind judge verdicts.
4. **Does evolution help when round-one evidence is genuinely insufficient?** Unknown, because no
   such test case exists yet. This one is upstream of the other three.
