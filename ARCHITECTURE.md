# Architecture & Codebase Guide — Deep Research Agent

> **Thesis:** *Spend more compute where evidence is thin; calibrate confidence honestly.*
> This document is a comprehensive, file-by-file walkthrough of how the Deep Research Agent works and how every module fits together.

---

## Table of Contents

1. [Project Purpose & Two-Track Design](#1-project-purpose--two-track-design)
2. [Repository Layout](#2-repository-layout)
3. [End-to-End Data Flow](#3-end-to-end-data-flow)
4. [Entry Point: `run.py`](#4-entry-point-runpy)
5. [Configuration: `configs/` + `src/orchestrator/config.py`](#5-configuration-configs--srcorchestratorconfigpy)
6. [State Model: `src/orchestrator/state.py`](#6-state-model-srcorchestratorstatepy)
7. [Pipeline Orchestrator: `src/orchestrator/pipeline.py`](#7-pipeline-orchestrator-srcorchestratorpipelinepy)
8. [Adaptive Compute Allocator: `src/orchestrator/allocator.py`](#8-adaptive-compute-allocator-srcorchestratorallocatorpy)
9. [Agents: `src/agents/`](#9-agents-srcagents)
   - 9.1 [Planner](#91-planner--srcagentsplannerpy)
   - 9.2 [Researcher](#92-researcher--srcagentsresearcherpy)
   - 9.3 [Extractor](#93-extractor--srcagentsextractorpy)
   - 9.4 [Synthesizer](#94-synthesizer--srcagentssynthesizerpy)
10. [Retrieval Tools: `src/tools/`](#10-retrieval-tools-srctools)
    - 10.1 [LLM Client (`base.py`)](#101-llm-client--srctoolsbasepy)
    - 10.2 [arXiv (`arxiv.py`)](#102-arxiv--srctoolsarxivpy)
    - 10.3 [Web Search (`search.py`)](#103-web-search--srctoolssearchpy)
    - 10.4 [PDF (`pdf.py`)](#104-pdf--srctoolspdpy)
    - 10.5 [Mock LLM (`mock_llm.py`)](#105-mock-llm--srctoolsmock_llmpy)
11. [Chunking: `src/retrieval/chunker.py`](#11-chunking-srcretrievalchunkerpy)
12. [Scoring: `src/scoring/`](#12-scoring-srcscoring)
    - 12.1 [Difficulty Estimator](#121-difficulty-estimator--srcscoringdifficultypy)
    - 12.2 [Verifier](#122-verifier--srcscoringverifierpy)
    - 12.3 [Confidence Scorer](#123-confidence-scorer--srcscoringconfidencepy)
13. [Observability: `src/obs/trace.py`](#13-observability-srcobstracepy)
14. [Evaluation: `eval/`](#14-evaluation-eval)
15. [Ablations: `ablations/`](#15-ablations-ablations)
16. [Outputs & Artifacts](#16-outputs--artifacts)
17. [Dependency Graph](#17-dependency-graph)
18. [How to Run](#18-how-to-run)
19. [Design Decisions Summary](#19-design-decisions-summary)
20. [Design Rationale & Results: Architecture ↔ Research Directions](#20-design-rationale--results-architecture--research-directions)
    - 20.1 [Search-and-Reason Interleaving (ReAct, Search-R1)](#201-search-and-reason-interleaving-react-search-r1)
    - 20.2 [Process Reward Models: Step-Level Verification](#202-process-reward-models-step-level-verification)
    - 20.3 [Self-Correction (SCoRe): Per-Claim Revision Loops](#203-self-correction-score-per-claim-revision-loops)
    - 20.4 [Test-Time Compute Scaling: Adaptive Budgets](#204-test-time-compute-scaling-adaptive-budgets)
    - 20.5 [Multi-Agent Debate and Review: De-scoped](#205-multi-agent-debate-and-review-de-scoped)
    - 20.6 [Dynamic Tool Selection: Partial Implementation](#206-dynamic-tool-selection-partial-implementation)
    - 20.7 [Context Management](#207-context-management)
    - 20.8 [Infrastructure & Observability: Trace System](#208-infrastructure--observability-trace-system)
    - 20.9 [Testing Directions: What Was Built and What Wasn't](#209-testing-directions-what-was-built-and-wasnt)
    - 20.10 [Cross-Track Questions: How the Architecture Answers Them](#210-cross-track-questions-how-the-architecture-answers-them)

---

## 1. Project Purpose & Two-Track Design

The Deep Research Agent is an LLM-driven research pipeline that answers complex questions by decomposing them into sub-questions, retrieving evidence from arXiv + the web, extracting and verifying claims, and synthesizing a cited markdown report. It is built around two mutually-reinforcing "tracks":

| Track | Name | Core Idea | Key Modules |
|-------|------|-----------|-------------|
| **A** | Adaptive Test-Time Compute | Spend more retrieval rounds on harder sub-questions; stop early on easy ones. | `allocator.py`, `difficulty.py` |
| **B** | Evidence-Grounded Verification | Verify every claim against its evidence; detect contradictions; calibrate confidence honestly. | `verifier.py`, `confidence.py` |

The two tracks are coupled: **verifier confidence feeds back into difficulty estimation**, which in turn drives compute allocation. This creates a closed feedback loop — the central architectural idea of the project.

---

## 2. Repository Layout

```
deep-research-agent/
├── run.py                       # CLI entry point — all modes start here
├── pyproject.toml               # Dependencies & build config
├── configs/
│   ├── default.yaml             # OpenAI-backed config (gpt-4o-mini / gpt-4o)
│   └── openrouter.yaml          # OpenRouter free-model config (deepseek-r1:free)
├── src/
│   ├── orchestrator/
│   │   ├── config.py            # Typed dataclass config loaded from YAML
│   │   ├── state.py             # Pydantic data models (the "state" object)
│   │   ├── pipeline.py          # Main 4-phase pipeline orchestrator
│   │   └── allocator.py         # Track A: difficulty → compute budget
│   ├── agents/
│   │   ├── planner.py           # Decomposes query into sub-questions
│   │   ├── researcher.py        # One retrieval round (arXiv + web → chunks)
│   │   ├── extractor.py         # Extracts atomic claims from evidence
│   │   └── synthesizer.py       # Writes the final markdown report
│   ├── tools/
│   │   ├── base.py              # LLMClient (OpenAI / OpenRouter) + JSON fallback
│   │   ├── arxiv.py             # arXiv search + PDF/abstract fetch
│   │   ├── search.py            # Multi-backend web search (DDG/Wikipedia/Tavily)
│   │   ├── pdf.py               # pypdf-based PDF text extraction
│   │   └── mock_llm.py          # Deterministic mock LLM for CI/offline tests
│   ├── retrieval/
│   │   └── chunker.py           # Fixed-size overlapping text chunking
│   ├── scoring/
│   │   ├── difficulty.py        # Two-phase difficulty estimation
│   │   ├── verifier.py          # Per-claim LLM verification + contradiction detection
│   │   └── confidence.py        # Calibrated confidence scoring
│   └── obs/
│       └── trace.py             # Trace logging, timing, state persistence
├── eval/
│   ├── harness.py               # Runs all test cases, collects metrics
│   ├── metrics.py               # 4-metric evaluation (support rate, ECE, etc.)
│   ├── test_cases.py            # 7 stress-test cases
│   └── visualize.py             # Generates "wow" figures (cost-quality, reliability)
├── ablations/
│   └── run_ablation.py          # 3-mode ablation study (adaptive / uniform / no-verify)
├── outputs/                     # Generated reports (gitignored)
│   ├── demo_mock/
│   └── demo_real/
└── ablations/results/           # Ablation artifacts (gitignored)
```

**Line counts (core source):**

| File | Lines | Role |
|------|-------|------|
| `run.py` | 118 | CLI entry |
| `src/orchestrator/pipeline.py` | 148 | Core orchestrator |
| `src/orchestrator/state.py` | 92 | Data models |
| `src/orchestrator/config.py` | 100 | Config dataclasses |
| `src/orchestrator/allocator.py` | 145 | Track A allocator |
| `src/agents/planner.py` | 67 | Query decomposition |
| `src/agents/researcher.py` | 90 | Retrieval round |
| `src/agents/extractor.py` | 90 | Claim extraction |
| `src/agents/synthesizer.py` | 112 | Report synthesis |
| `src/tools/base.py` | 164 | LLM client + JSON fallback |
| `src/tools/arxiv.py` | 132 | arXiv API + PDF fetch |
| `src/tools/search.py` | 218 | Multi-backend web search |
| `src/tools/pdf.py` | 64 | PDF extraction |
| `src/tools/mock_llm.py` | 104 | Mock LLM for tests |
| `src/scoring/difficulty.py` | 136 | Difficulty estimation |
| `src/scoring/verifier.py` | 163 | Claim verification + contradictions |
| `src/scoring/confidence.py` | 87 | Confidence calibration |
| `src/retrieval/chunker.py` | 56 | Text chunking |
| `src/obs/trace.py` | 69 | Tracing/timing |
| `eval/harness.py` | ~80 | Eval runner |
| `eval/metrics.py` | ~100 | Metrics computation |
| `eval/visualize.py` | 160 | Plot generation |
| `ablations/run_ablation.py` | ~90 | Ablation runner |

---

## 3. End-to-End Data Flow

```
                         ┌─────────┐
                         │  Query  │  (user question, e.g. "How do Transformers compare to RNNs?")
                         └────┬────┘
                              │
              ┌───────────────┼──────────────────────────────┐
              │           PHASE 1: PLAN                      │
              │                                               │
              │  planner.py → plan(state, sub_llm, config)   │
              │  LLM decomposes query into 3-5 sub-questions │
              │  Output: ResearchPlan (clarified_query +     │
              │          list[SubQuestion])                   │
              └───────────────┬──────────────────────────────┘
                              │
              ┌───────────────┼──────────────────────────────┐
              │           PHASE 2: ADAPTIVE RETRIEVAL LOOP   │
              │  (per sub-question)                           │
              │                                               │
              │  ┌────────────────────────────────────────┐  │
              │  │ 2a. estimate_difficulty(sq)            │  │  Track A (linguistic)
              │  │ 2b. allocate_budget(difficulty)         │  │  Track A (allocator)
              │  │                                          │  │
              │  │  while rounds_used < budget:            │  │
              │  │   ┌──────────────────────────────────┐  │  │
              │  │   │ 2c. research_sub_question(sq)    │  │  │  researcher.py
              │  │   │     → arXiv search + web search  │  │  │  arxiv.py + search.py
              │  │   │     → fetch PDF / abstract       │  │  │  pdf.py
              │  │   │     → chunk_text() → Evidence[]  │  │  │  chunker.py
              │  │   └──────────────┬───────────────────┘  │  │
              │  │                  │                       │  │
              │  │   ┌──────────────▼───────────────────┐  │  │
              │  │   │ 2d. extract_claims(evidence)     │  │  │  extractor.py
              │  │   │     LLM → list[Claim]            │  │  │
              │  │   └──────────────┬───────────────────┘  │  │
              │  │                  │                       │  │
              │  │   ┌──────────────▼───────────────────┐  │  │  Track B
              │  │   │ 2e. verify_claims(claims, ev)    │  │  │  verifier.py
              │  │   │     LLM → support_score + status │  │  │
              │  │   └──────────────┬───────────────────┘  │  │
              │  │                  │                       │  │
              │  │   ┌──────────────▼───────────────────┐  │  │  Track B
              │  │   │ 2f. score_confidence(claims)     │  │  │  confidence.py
              │  │   │     → calibrated confidence      │  │  │
              │  │   └──────────────┬───────────────────┘  │  │
              │  │                  │                       │  │
              │  │   ┌──────────────▼───────────────────┐  │  │  Track A↔B link
              │  │   │ 2g. update_difficulty(confidence)│  │  │  difficulty.py
              │  │   │ 2h. should_continue?             │  │  │  allocator.py
              │  │   │     → extend budget or stop      │  │  │
              │  │   └──────────────────────────────────┘  │  │
              │  └────────────────────────────────────────┘  │
              └───────────────┬──────────────────────────────┘
                              │
              ┌───────────────┼──────────────────────────────┐
              │           PHASE 3: CROSS-CLAIM ANALYSIS     │
              │                                               │
              │  detect_contradictions(claims)               │  verifier.py
              │  score_confidence (re-score with penalties)  │  confidence.py
              └───────────────┬──────────────────────────────┘
                              │
              ┌───────────────┼──────────────────────────────┐
              │           PHASE 4: SYNTHESIS                 │
              │                                               │
              │  synthesize(state, synthesis_llm, config)    │  synthesizer.py
              │  LLM writes markdown report from verified    │
              │  claims, grouped by sub-question              │
              │  Output: state.report (markdown string)       │
              └───────────────┬──────────────────────────────┘
                              │
                         ┌────▼────┐
                         │ Outputs │  trace.jsonl + state.json + report.md
                         └─────────┘
```

The **critical feedback loop** is inside Phase 2: verifier confidence (Track B) feeds back into difficulty estimation, which feeds back into the compute budget (Track A). This is what makes the two tracks "mutually reinforcing" rather than independent.

---

## 4. Entry Point: `run.py`

`run.py` is the single CLI entry point for all modes. It is intentionally lightweight — it wires together config loading and the pipeline, then prints results.

### Key functions

| Function | Purpose |
|----------|---------|
| `_load_env_file(path=".env")` | Dependency-free `.env` loader. Reads `KEY=VALUE` lines, populates `os.environ` with `setdefault` (won't override existing env vars). Added in the `002-free-model-websearch-pdf` branch so users don't need `python-dotenv` or manual `export`. |
| `_load_config(args)` | Selects config file: `--openrouter` → `configs/openrouter.yaml`, else `--config <path>` or default `configs/default.yaml`. Returns a `Config` object. |
| `main()` | Argparse CLI. Dispatches to the appropriate mode. |

### CLI modes

| Flag | Mode | What it does |
|------|------|--------------|
| *(positional query)* | Single query | Runs `run_research(query, config, output_dir)` and prints stats + report preview. |
| `--demo` | Demo | Uses a built-in query ("How do Transformer architectures compare to RNNs for sequence modeling?") for quick smoke-testing. |
| `--eval` | Evaluation | Runs `eval.harness.run_eval()` across all 7 test cases. |
| `--ablation` | Ablation | Runs `ablations.run_ablation.run_ablation()` comparing adaptive vs uniform vs no-verify. |
| `--mock` | Mock LLM | Uses `MockLLMClient` instead of real API calls — no API key needed. Works with any mode. |
| `--openrouter` | OpenRouter | Selects `configs/openrouter.yaml` (free deepseek-r1:free model). |

### Output statistics printed

After a run, `run.py` prints: total claims, evidence chunks, contradictions, total tokens, total latency, and a preview of the report.

---

## 5. Configuration: `configs/` + `src/orchestrator/config.py`

### Config files

**`configs/default.yaml`** — OpenAI backend:
- `sub_step_model`: `gpt-4o-mini` (used for planning, extraction, verification)
- `synthesis_model`: `gpt-4o` (used for final report)
- `temperature`: 0.3, `max_tokens`: 2000
- Retrieval: 5 results/query, max 3 rounds, 1500-char chunks with 200 overlap
- Adaptive: enabled, budget 1-4, low-confidence threshold 0.5, novelty threshold 0.15
- Verification: enabled, verifier model `gpt-4o-mini`, support threshold 0.4
- Synthesis: include gaps, markdown output

**`configs/openrouter.yaml`** — OpenRouter free model:
- `sub_step_model` + `synthesis_model`: both `deepseek/deepseek-r1:free`
- `max_tokens`: 4000 (free models need more room for reasoning)
- `base_url`: `https://openrouter.ai/api/v1`
- `api_key_env`: `OPENROUTER_API_KEY`
- Same retrieval/adaptive/verification/synthesis settings
- **Important:** free models don't support `response_format: json_object`, so `LLMClient.complete_json` falls back to plain-text + regex JSON extraction (see §10.1).

### `src/orchestrator/config.py`

Typed configuration via nested dataclasses:

```
Config
├── llm: LLMConfig(sub_step_model, synthesis_model, temperature, max_tokens, base_url, api_key_env)
├── retrieval: RetrievalConfig(search_results_per_query=5, max_rounds=3, chunk_size=1500, chunk_overlap=200)
├── adaptive: AdaptiveConfig(enabled=True, min_budget=1, max_budget=4, low_confidence_threshold=0.5, novelty_threshold=0.15)
├── verification: VerificationConfig(enabled=True, verifier_model, support_threshold=0.4)
├── synthesis: SynthesisConfig(include_gaps=True, output_format='markdown')
└── eval: EvalConfig(calibration_bins=10)
```

`Config.load(path)` reads YAML via `yaml.safe_load` and falls back to defaults for missing fields. `Config.from_dict()` parses a dict into the nested dataclass structure. This provides type safety and IDE autocomplete over raw YAML.

---

## 6. State Model: `src/orchestrator/state.py`

All data flowing through the pipeline is represented as **Pydantic models** (decision D001). This gives validation, JSON serialization, and type safety for free.

### Models

| Model | Fields | Purpose |
|-------|--------|---------|
| `EvidenceChunk` | `chunk_id`, `source_url`, `source_title`, `source_type` (`'arxiv'` or `'web'`), `text`, `offset_start/end`, `retrieval_round` | One chunk of retrieved text with full provenance. |
| `Claim` | `claim_id`, `text`, `evidence_ids`, `support_score` (0-1), `verification_status` (`'supported'`/`'contradicted'`/`'insufficient'`), `confidence`, `sub_question_id` | An atomic verifiable statement extracted from evidence. |
| `SubQuestion` | `sq_id`, `question`, `difficulty` (0-1), `compute_budget`, `rounds_used`, `claim_ids`, `sufficient_evidence` | One sub-question from the planner, with its difficulty/budget state. |
| `ResearchPlan` | `query`, `sub_questions`, `clarified_query` | The planner's output. |
| `Contradiction` | `claim_a_id`, `claim_b_id`, `description`, `source_a`, `source_b` | A detected contradiction between two claims from different sources. |
| `TraceEntry` | `timestamp`, `component`, `step`, `input_summary`, `output_summary`, `latency_ms`, `cost_tokens`, `metadata` | One step in the execution trace. |
| **`ResearchState`** | `query`, `plan`, `evidence`, `claims`, `contradictions`, `report`, `trace`, `total_tokens`, `total_latency_ms` | **The central state object** — flows through the entire pipeline, accumulating evidence, claims, and trace entries. |

`ResearchState` is the backbone: every agent reads from and writes to it. It is serialized to `state.json` at the end of a run.

---

## 7. Pipeline Orchestrator: `src/orchestrator/pipeline.py`

`run_research(query, config, output_dir, use_mock)` is the **main entry point** called by `run.py`. It implements the 4-phase pipeline:

### Phase 1: Plan
```python
plan(state, sub_llm, config)
```
The planner LLM decomposes the query into 3-5 atomic sub-questions. Populates `state.plan`.

### Phase 2: Adaptive Retrieval Loop (per sub-question)
For each `SubQuestion` in `state.plan.sub_questions`:
1. `estimate_difficulty(sq)` — linguistic difficulty estimate (Track A)
2. `allocate_budget(difficulty)` — map difficulty to compute budget (Track A)
3. **While** `rounds_used < budget`:
   - `research_sub_question(state, sq, round_num, config)` — retrieve + chunk evidence
   - `extract_claims(state, sq, evidence_chunks, sub_llm, config)` — LLM extracts claims
   - `verify_claims(state, claims, verifier_llm, config)` — LLM verifies each claim (Track B)
   - `score_confidence(state, claims, config)` — calibrate confidence (Track B)
   - `update_difficulty(state, sq, config)` — re-estimate difficulty from confidence (Track A↔B link)
   - `should_continue(state, sq, config)` — extend budget or stop (Track A)
4. After loop: `allocate_initial_budgets` for batch uniform mode if adaptive disabled

**Adaptive vs Uniform:** Controlled by `config.adaptive.enabled`:
- **Adaptive mode:** Budget varies by difficulty (1-4 rounds). Low-confidence triggers extension.
- **Uniform mode:** All sub-questions get `max_budget` (4 rounds) regardless of difficulty.

### Phase 3: Cross-Claim Analysis
- `detect_contradictions(state, claims, llm, config)` — cross-source contradiction detection
- `score_confidence(state, claims, config)` — re-score confidence with contradiction penalties

### Phase 4: Synthesis
```python
synthesize(state, synthesis_llm, config)
```
The synthesis LLM writes the final markdown report.

### Output persistence
After the pipeline completes, three files are written to `output_dir`:
- `trace.jsonl` — one `TraceEntry` per line (via `save_trace`)
- `state.json` — full `ResearchState.model_dump()` (via `save_full_state`)
- `report.md` — the final markdown report

---

## 8. Adaptive Compute Allocator: `src/orchestrator/allocator.py`

**Track A core module.** Maps sub-question difficulty to a compute budget (number of retrieval rounds).

### `allocate_budget(difficulty, config) → int`
Linear interpolation: difficulty 0 → `min_budget` (1), difficulty 1 → `max_budget` (4).
```python
budget = min_budget + difficulty * (max_budget - min_budget)
```

### `should_continue(state, sq, config) → bool`
After each round, checks if the sub-question needs more retrieval:
- If average claim confidence < `low_confidence_threshold` (0.5): update difficulty from confidence, extend budget up to `max_budget`.
- This is the **feedback loop**: low confidence → higher difficulty → more compute.

### `allocate_initial_budgets(plan, config) → dict`
Batch allocation after planning. In uniform mode, all sub-questions get `max_budget`.

---

### 8.1 Design Rationale: Test-Time Compute Scaling **[S4]**

**The research question.** The Notion "Advanced Areas of Pursuit" plan identifies test-time compute scaling as the strongest recommendation: *spend more compute at inference time on harder problems, rather than training a larger model.* The literature (Snell et al., "Scaling Test-Time Compute," 2024) shows that adaptive compute — where the system chooses how much to think based on problem difficulty — can outperform uniform compute at the same total budget.

**What we built and why.** The allocator implements the simplest possible adaptive policy: a linear map from difficulty (0-1) to retrieval rounds (1-4). We chose linear interpolation over a learned policy because:

1. **Transparency.** A linear function is fully auditable — you can predict the budget for any difficulty without running the system. This matters for the assignment's "probe your understanding" criterion.
2. **No training data required.** A learned policy (e.g., a small neural net mapping difficulty → budget) would need training examples, which we don't have. The linear map uses domain knowledge: harder questions get more rounds.
3. **The feedback loop is where the intelligence lives.** The allocator's initial budget is a crude estimate. The real adaptivity comes from `should_continue`: after each round, confidence is re-estimated, difficulty is updated, and the budget can be extended. This is a *runtime* adaptive policy, not a *static* one.

**What it produced.** The ablation (§15) shows adaptive mode achieves 98% support rate at 31K tokens average, while uniform mode achieves 99% at 110K tokens — a **3.5× cost reduction** for a 1 percentage point quality trade-off.

> ⚠️ **Superseded in part — read D036.** That comparison uses the *maximum*-compute baseline (4 rounds on every sub-question). A later run added the *minimum*-compute baseline (1 round each), which D011 had flagged as untested: it reached **100% support on fewer tokens than either adaptive arm**. The claim above holds against max-uniform and fails against min-uniform, so adaptivity is worth paying for only if the alternative is spending maximum everywhere. The sentence originally here — "it is not possible to get to 98% quality with fewer tokens using uniform allocation" — is **false as measured**, and is left struck through rather than deleted because being wrong in a documented, testable way is the point of this log.

**What it did not solve.** The linear policy is not optimal. A truly adaptive system would use a non-linear function (e.g., sigmoid or step function) that sharply increases compute only above a difficulty threshold. The feedback loop also has a limitation: it can only *extend* the budget, not *reduce* it. If the initial estimate is too high (easy question gets budget=3), the system wastes two rounds before the confidence-based early-stop kicks in. A bidirectional policy (extend when confidence is low, stop early when confidence is high) would be more efficient — and is partially implemented via `should_continue`, which does stop early if confidence exceeds the threshold.

---

## 9. Agents: `src/agents/`


Each agent is a single function that takes the shared `state`, an `LLMClient`, and `config`, and mutates `state` in place.

### 9.1 Planner — `src/agents/planner.py`

```python
plan(state, llm, config) → None
```

- **Input:** `state.query`
- **LLM call:** System prompt asks for JSON `{clarified_query, sub_questions: [{question}]}`
- **Output:** Creates `SubQuestion(sq_id=f'sq_{i}', question)` objects and a `ResearchPlan`, sets `state.plan`
- **Trace:** Logs step `'planner.decompose'`
- **Goal:** Break a complex query into 3-5 atomic, independently-answerable sub-questions.

### 9.2 Researcher — `src/agents/researcher.py`

```python
research_sub_question(state, sq, round_num, config) → list[EvidenceChunk]
```

- **Input:** `sq.question`, `round_num`
- **Retrieval:**
  1. `search_arxiv(sq.question, max_results)` → list of arXiv papers
  2. `fetch_arxiv_content(paper)` → full PDF text (or abstract fallback)
  3. `web_search(sq.question, max_results)` → web results (multi-backend)
- **Chunking:** `chunk_text()` on each result's text, tagged with source provenance
- **State mutation:** Appends chunks to `state.evidence`, increments `sq.rounds_used`
- **Trace:** Logs step `'researcher.round_{round_num}'`

This is the only agent that touches external retrieval tools (arXiv, web, PDF).

### 9.3 Extractor — `src/agents/extractor.py`

```python
extract_claims(state, sq, evidence_chunks, llm, config) → list[Claim]
```

- **Input:** Evidence chunks for one sub-question (text truncated to 800 chars each)
- **LLM call:** System prompt asks for JSON `{claims: [{text, evidence_indices: [0, 1]}]}`
- **Output:** Creates `Claim(claim_id=f'claim_{N+i}', text, evidence_ids, sub_question_id=sq.sq_id)` objects
- **State mutation:** Appends to `state.claims` and `sq.claim_ids`
- **Trace:** Logs step `'extractor.extract'`
- **Goal:** Extract atomic, verifiable claims — each claim linked to its supporting evidence chunks.

### 9.4 Synthesizer — `src/agents/synthesizer.py`

```python
synthesize(state, llm, config) → None
```

- **Input:** All verified claims grouped by sub-question, with confidence scores, verification status, contradictions
- **LLM call:** System prompt instructs:
  - Claims > 0.6 confidence → present as facts
  - Claims < 0.6 confidence → frame as uncertain
  - Sections: Executive Summary, Findings (per sub-question), Contradictions & Disagreements, Known Gaps & Limitations
- **Output:** Sets `state.report` (markdown string)
- **Trace:** Logs step `'synthesizer.synthesize'`
- **Key design:** The synthesizer is the **only** agent that uses the `synthesis_model` (gpt-4o / deepseek-r1:free). All other agents use `sub_step_model`.

### 9.5 Challenger — `src/agents/challenger.py`

```python
challenge_claim(state, claim, evidence_pool, llm, config, round_num) → ChallengeResult
```

Closes the gap the original pipeline left open: the extractor is append-only, so a claim written in round 1 never meets round-3 evidence that undercuts it. The challenger runs after every round and attacks each active claim for its sub-question against the **full accumulated evidence pool** (not just the chunks the claim cites), on a model deliberately different from the one that wrote the claim (`config.evolution.challenger_model`).

- Scores `reasoning_score` (0–1) — is the claim a *warranted inference*, as distinct from the verifier's `support_score`, which only asks whether the cited text entails the claim. A claim can restate one chunk faithfully and still be an unsound generalization.
- Returns `supporting_evidence_indices` / `refuting_evidence_indices`, converted to **distinct source counts** (`compute_evidence_balance`) — a paper split into forty chunks must not outvote three papers that disagree with it.
- Evidence pool is sampled via `select_challenge_evidence` (in `evolution.py`) when it exceeds `max_evidence_chunks`, round-robining across sources so the sample stays representative rather than being forty chunks of one PDF.
- Does not rewrite claims — diagnosis only. Logged as `challenger.challenge` and appended to `state.challenges` regardless of verdict, including claims that survive unchanged (needed to measure self-agreement bias — see §20.11).

### 9.6 Reviser — `src/agents/reviser.py`

```python
revise_claim(state, claim, challenge, operation, evidence_pool, llm, config, round_num) → RevisionResult
```

Executes exactly the operation it's told (`keep|refine|narrow|reverse|retract`) — it does not decide which one. The decision is made deterministically by `route_operation` in `src/orchestrator/evolution.py`, thresholding the challenger's evidence balance:

```
balance < reversal_threshold (-0.3)      → reverse (or retract if unsupported)
balance <= nuance_threshold (0.5)        → narrow
reasoning_score < soundness_threshold    → refine
else                                     → keep
```

Splitting diagnosis (challenger) from repair (reviser) means the router — not either model — decides whether a claim's *position* is allowed to change; the reviser can escalate to `retract` if it finds the claim unsalvageable while rewriting, but cannot downgrade an assigned reversal into something more convenient. Every non-retract revision is re-verified via `verify_claims`, so `support_score` always describes the current text, not the text that earned the earlier score.

`evolve_claims` (in `evolution.py`) orchestrates the pass: selects which claims to challenge this round (lowest-confidence first, capped at `max_challenges_per_round`, skipping claims `frozen` after `stability_rounds` consecutive "keep" verdicts), runs challenge → route → revise → re-verify, and logs a per-round summary. `should_continue` (allocator.py) also checks `_claims_churned_last_round` — a sub-question whose claims are still being revised has not converged, independent of confidence.

See DECISIONS.md D020/D021 for the design rationale and the self-agreement-bias ablation.

### 9.7 Query Reformulator — `src/agents/query_reformulator.py`

```python
reformulate_query(state, sq, round_num, llm, config) → (query, rationale, gap)
```

Fixes a concrete defect found in the 7-test-case evaluation: `research_sub_question` passed `sq.question` **verbatim** to arXiv and web search on *every* round, so round 3 issued the identical query round 1 did and only paged deeper into the same ranked results. Extra rounds added evidence *volume*, not new *angles* — a large part of why accumulating rounds barely moved confidence (D023).

- Round 1 uses the sub-question verbatim; rounds 2+ generate a query targeting what earlier rounds missed.
- **Context compaction:** the reformulator never sees the raw evidence pool (hundreds of chunks, growing each round). It sees a digest built from `SubQuestion.retrieval_attempts` — prior queries, returned source titles, and how the resulting claims scored — plus the weakest standing claims. Cheap enough to run every round.
- Guards against the two degenerate outputs (empty query; echoing a previously-tried query) by falling back to the sub-question and logging it.
- Every attempt is recorded to `sq.retrieval_attempts`, making the whole search trajectory auditable in `state.json` rather than invisible inside a loop.

### 9.8 Report Critic — `src/agents/report_critic.py`

```python
critique_report(state, mechanical_defects, llm, config, pass_num) → ReportCritique
```

The only stage that asks whether the **assembled report** answers the question that was actually asked. Everything upstream is per-claim: the verifier asks "does the evidence entail this claim," the challenger asks "is this claim warranted." A report built entirely from well-verified claims can still bury the answer, overstate a 0.4-confidence claim, skip a sub-question, or cite a retracted claim. Runs on an **independent model** (reuses the challenger's client).

Emits `verdict ∈ {accept, revise_report, needs_more_research}`, a list of typed `ReportDefect`s, and — for `needs_more_research` — actionable `ResearchGap`s naming *what to go find*. A `needs_more_research` verdict with no actionable gap is downgraded to `revise_report` rather than triggering a directionless reopen.

---

## 9A. Phase 5: Report Self-Correction — `src/orchestrator/report_loop.py`

```python
run_report_correction(state, synth_llm, critic_llm, sub_llm, challenger_llm, reviser_llm, config) → None
```

**Two tiers of error detection.** Tier 1 is `mechanical_checks()` — deterministic, LLM-free, and run *first*:

| Check | Detection |
|---|---|
| Retracted claim still asserted | fuzzy word-overlap (≥0.8) of a retracted claim against report paragraphs — a hard error |
| Sub-question with zero surviving claims | `active_claims_for(sq) == []`, severity raised if no gaps section exists |
| Thin sub-question | avg confidence below `thin_confidence_threshold` |
| Contradictions detected but never discussed | `state.contradictions` non-empty, no "contradict"/"disagree" in report |

These findings are passed to the critic as *established facts*, so the LLM spends attention on judgment calls (overstatement, burial, "does this answer the question") instead of re-deriving what a substring check already proved.

**The join with query reformulation.** On `needs_more_research`, each gap's `what_to_find` is written into the target sub-question's `retrieval_attempts[-1].gap_noted` — which the reformulator (§9.7) already consumes. The critic says *what's missing*; the reformulator turns it into *a different query*; `retrieval_attempts` is the compacted memory of what was already tried. That is the Search-R1 "learn from retrieval mistakes" loop closing end-to-end.

**Three independent brakes**, because a loop that can reopen retrieval is the most expensive thing in the pipeline:
1. `max_passes` (default 2) — hard cap.
2. **One reopen per sub-question, ever** — afterwards it can only be fixed by rewriting.
3. `stop_when_not_improving` — a pass that doesn't reduce high-severity defects stops the loop. D023 established that a critic finding *more* fault is not evidence it is right, so "keeps complaining" must terminate rather than justify another pass.

---

## 10. Retrieval Tools: `src/tools/`

### 10.1 LLM Client — `src/tools/base.py`

The `LLMClient` wraps the OpenAI Python SDK and supports both OpenAI and OpenRouter.

**`LLMResponse`** dataclass: `text`, `input_tokens`, `output_tokens`, `latency_ms`, `total_tokens` (property = input + output).

**`complete(system, user) → LLMResponse`**: Standard chat completion.

**`complete_json(system, user) → (dict, LLMResponse)`**: Structured JSON completion with a **two-tier fallback**:
1. **First attempt:** `response_format={"type": "json_object"}` — native JSON mode (OpenAI gpt-4o, etc.)
2. **Fallback:** Plain-text completion + `_extract_json_from_text()` regex extractor — for free models (deepseek-r1:free) that don't support JSON mode. The extractor handles:
   - ```` ```json ... ``` ```` fenced code blocks
   - Bare `{...}` with balanced-brace matching

This fallback was added in the `002-free-model-websearch-pdf` branch specifically for OpenRouter free models.

**OpenRouter support:** When `config.llm.base_url` is set (e.g. `https://openrouter.ai/api/v1`), the client passes it to the OpenAI SDK. API key is read from the env var named in `config.llm.api_key_env` (e.g. `OPENROUTER_API_KEY` or `OPENAI_API_KEY`).

### 10.2 arXiv — `src/tools/arxiv.py`

**`ArxivPaper`** dataclass: `arxiv_id`, `title`, `authors`, `abstract`, `url`, `pdf_url`, `published`.

| Function | Purpose |
|----------|---------|
| `search_arxiv(query, max_results=5)` | Queries the public arXiv API (Atom XML, no auth). Returns `list[ArxivPaper]`. |
| `fetch_arxiv_abstract(paper)` | Returns the abstract string (always available). |
| `fetch_arxiv_fulltext(paper, max_pages=30)` | Downloads the PDF via `pdf.fetch_pdf_text`, extracts text. Falls back to abstract on any failure. |
| `fetch_arxiv_content(paper)` | **Primary entry point** — PDF-first, abstract fallback. Used by `researcher.py`. |

arXiv is the **primary** source (decision D003); web search is secondary.

### 10.3 Web Search — `src/tools/search.py`

Multi-backend web search, selected via the `SEARCH_BACKEND` env var (default: `'auto'`).

**`SearchResult`** dataclass: `title`, `url`, `content`, `source_type='web'`.

| Backend | Function | Auth | Notes |
|---------|----------|------|-------|
| `tavily` | `_search_tavily()` | `TAVILY_API_KEY` | POST to `api.tavily.com`, `include_raw_content=true`. Falls back to `auto` if no key. |
| `duckduckgo` | `_search_duckduckgo()` | None | HTML scraping via BeautifulSoup. POST to `html.duckduckgo.com/html/`. `_unwrap_ddg_url()` extracts real URL from DDG redirect wrapper. |
| `wikipedia` | `_search_wikipedia()` | None | MediaWiki `action=query` API. Always free, no key. |
| `auto` | (combination) | None | DDG first, fills remaining slots with Wikipedia. Dedup by URL. |

This multi-backend design was added in the `002-free-model-websearch-pdf` branch to enable free-tier operation without paid search APIs.

### 10.4 PDF — `src/tools/pdf.py`

pypdf-based PDF text extraction. All functions **degrade gracefully** — they return `''` on any failure, so the pipeline never crashes on a malformed PDF.

| Function | Purpose |
|----------|---------|
| `read_pdf_bytes(data, max_pages=50)` | Extract text from in-memory PDF bytes. |
| `read_pdf_file(path, max_pages=50)` | Extract text from a local PDF file. |
| `fetch_pdf_text(url, max_pages=50, timeout=30)` | Download PDF via `requests`, extract text. Returns `''` on any failure. |

Used by `arxiv.py` to fetch full-text from arXiv PDFs.

### 10.5 Mock LLM — `src/tools/mock_llm.py`

`MockLLMClient` — a **drop-in replacement** for `LLMClient` that requires no API keys. Enables full pipeline testing in CI and offline development.

- `complete()` → returns a mock markdown report
- `complete_json()` → returns deterministic mock data based on system prompt content:
  - `_mock_plan()` — 3 sub-questions
  - `_mock_claims()` — 3 claims with evidence indices
  - `_mock_verification()` — hash-based `support_score` (0.1-0.95)
  - `_mock_contradictions()` — occasionally returns one

Activated via `--mock` flag in `run.py`.

---

## 11. Chunking: `src/retrieval/chunker.py`

```python
chunk_text(text, source_url, source_title, source_type,
            chunk_size=1500, chunk_overlap=200,
            retrieval_round, chunk_id_prefix) → list[EvidenceChunk]
```

Fixed-size overlapping chunking (decision D009):
- **Chunk size:** 1500 characters (from `config.retrieval.chunk_size`)
- **Overlap:** 200 characters (from `config.retrieval.chunk_overlap`)
- Overlap prevents splitting key sentences across chunk boundaries
- Each chunk is tagged with full provenance: `source_url`, `source_title`, `source_type`, `retrieval_round`
- `chunk_id` is generated from `chunk_id_prefix` + index

This is the bridge between raw retrieved text and the `EvidenceChunk` model used throughout the pipeline.

---

## 12. Scoring: `src/scoring/`

### 12.1 Difficulty Estimator — `src/scoring/difficulty.py`

**Two-phase difficulty estimation** (decision D005) — this is the **Track A ↔ Track B link**.

**Phase 1: `estimate_difficulty_linguistic(sq) → float (0-1)`**
Pre-retrieval estimate using linguistic cues:
- `HARD_KEYWORDS`: `vs`, `versus`, `contradict`, `debate`, `compare`, `disagree` → harder
- `EASY_KEYWORDS`: `what is`, `define`, `describe`, `explain` → easier
- Query length and question type also factor in

**Phase 2: `update_difficulty_from_confidence(state, sq, config) → float`**
Post-retrieval re-estimate:
```python
confidence_difficulty = 1.0 - avg_claim_confidence
updated = 0.6 * confidence_difficulty + 0.4 * linguistic_difficulty
```
This is the critical link: **verifier confidence IS the allocator difficulty signal**. Low confidence → high difficulty → more compute.

**Pipeline wrappers:** `estimate_difficulty(state, sq, config)` and `update_difficulty(state, sq, config)` wrap the two phases for the pipeline.

### 12.2 Verifier — `src/scoring/verifier.py`

**Track B core module.**

**`verify_claims(state, claims, llm, config) → list[Claim]`**
Per-claim LLM verification against evidence chunks:
- VERIFIER_SYSTEM prompt asks for JSON `{support_score, status, reasoning}`
- Uses `config.verification.verifier_model` (decision D006 — different model from `sub_step_model` to avoid self-agreement bias, though in practice the same model is often used due to cost)
- `_build_evidence_context()` builds evidence text (600-char cap per chunk)
- Sets `claim.support_score` and `claim.verification_status`
- Logs `'verifier.verify_claim'`

**`detect_contradictions(state, claims, llm, config) → list[Contradiction]`**
Cross-source contradiction detection:
- CONTRADICTION_SYSTEM prompt
- **Only flags claims from different sources** (no source intersection) — decision D010
- Creates `Contradiction` objects and appends to `state.contradictions`
- Logs `'verifier.detect_contradictions'`

### 12.3 Confidence Scorer — `src/scoring/confidence.py`

**`score_confidence(state, claims, config) → list[Claim]`**

Heuristic calibrated confidence (decision D007 — not temperature scaling):
```python
confidence = (0.5 * support + 0.3 * diversity) * (1.0 - contradiction_penalty)
```
- **Support:** from verifier's `support_score`
- **Diversity:** distinct `source_titles` in evidence — 1 source=0.5, 2=0.75, 3+=1.0
- **Contradiction penalty:** 0.3 if the claim is involved in a contradiction
- **Unverified claims:** get confidence = 0.3
- Logs `'confidence.score'`

This produces the `confidence` field on each `Claim`, which the synthesizer uses to decide whether to present claims as facts (>0.6) or uncertain (<0.6).


### 12.4 Design Rationale: The Verifier as a Lightweight Process Reward Model **[S2]**

**The research question.** The Notion plan asks: *can we evaluate intermediate steps, not just the final answer?* Process reward models (PRMs) in the literature (Lightman et al., "Let's Verify Step by Step," 2023) train a separate model to score each step of a reasoning chain, rather than scoring only the final output. The insight: a final-answer reward model cannot tell you *where* the reasoning went wrong, only *that* it went wrong.

**What we built and why.** Our verifier is not a trained PRM — it is a prompted LLM that scores each claim against its evidence. But it serves the same structural role:

| PRM (literature) | Our verifier |
|---|---|
| Trained on step-level human annotations | Prompted with verification instructions |
| Scores each reasoning step | Scores each extracted claim |
| Produces a scalar reward | Produces `support_score` (0-1) |
| Used to select or reject reasoning chains | Used to set confidence and flag contradictions |

We chose a prompted verifier over a trained PRM because:
1. **No training data.** We don't have step-level annotations for research-claim verification.
2. **The LLM is already capable.** GPT-4o-mini can verify claims against evidence when prompted clearly. The bottleneck is the prompt design, not model capacity.
3. **Transparency.** The verifier's reasoning is logged in the trace — you can read *why* it assigned a support score. A trained PRM's internal representations are opaque.

**What it produced.** The no-verify ablation (§15) is the key evidence: without the verifier, the support rate drops to **0%** — no claims are scored, no confidence is assigned, and the synthesizer has no signal to distinguish well-supported claims from unsupported ones. The verifier is load-bearing. With the verifier, the system achieves 92-100% support rate across test cases.

**What it did not solve.** A trained PRM would be more consistent — the prompted verifier's scores vary with prompt wording and model temperature. The verifier also verifies *claims against evidence*, not *reasoning steps*. If the extractor produces a claim that misinterprets the evidence, the verifier may score it as "supported" because the claim matches the evidence text — even though the extraction itself was flawed. A true PRM would score the extraction step separately from the verification step.

### 12.5 Design Rationale: Self-Correction and the Limits of Per-Claim Revision **[S3]**

**The research question.** The Notion plan references SCoRe (Kumar et al., "Training Language Models to Self-Correct via Reinforcement Learning," 2024) — the idea that a model should revise its own output when it detects errors. The full SCoRe approach trains the model to self-correct using RL; a simpler version uses a writer-reviewer loop: generate → critique → revise.

**What we built.** Our system has a per-claim revision loop, not a report-level revision loop. The loop is:
1. Extract claims from evidence
2. Verify each claim (score support)
3. If confidence is low, allocate more retrieval rounds (get more evidence)
4. Re-extract and re-verify

This is *evidence-level* self-correction: the system detects that its evidence is insufficient and retrieves more. It is *not* *reasoning-level* self-correction: the system does not critique its own synthesis and rewrite the report.

**Why per-claim, not report-level.** We chose per-claim correction because:
1. **Granular feedback.** A report-level revision loop would require the system to identify *which part* of the report is wrong. Per-claim correction gives localized feedback — each claim has its own confidence score, and low-confidence claims trigger more retrieval.
2. **Composability.** Per-claim correction composes with the adaptive compute loop (Track A). A report-level loop would be a separate process that runs after the retrieval loop, adding latency without improving the evidence base.
3. **The report is the last step.** The synthesizer (§9.4) takes verified claims and writes the report. If the claims are well-supported, the report will be accurate. If the claims are poorly supported, no amount of report-level revision will fix the underlying evidence gap.

**What it produced.** The adaptive ablation shows that per-claim correction (via the confidence feedback loop) achieves 98% support rate — the system successfully identifies under-evidenced claims and retrieves more evidence for them. The feedback loop is the mechanism by which this works.

**What it did not solve.** The system does not detect *synthesis errors* — cases where the individual claims are correct but the report misinterprets or misassembles them. A report-level self-correction loop (generate report → critique report → revise report) would catch these. We did not implement this because it would require a separate critique prompt and a second synthesis call, doubling the synthesis cost. The SCoRe literature suggests that self-correction is most valuable when the model can identify its own errors — but our system's errors are more often in *evidence retrieval* (did we find the right sources?) than in *synthesis* (did we assemble the claims correctly?).

---

## 13. Observability: `src/obs/trace.py`

| Function | Purpose |
|----------|---------|
| `log_step(state, component, step, input_summary, output_summary, latency_ms, cost_tokens, metadata)` | Appends a `TraceEntry` to `state.trace`, accumulates `state.total_tokens` and `state.total_latency_ms` |
| `save_trace(state, path)` | Writes `trace.jsonl` — one `TraceEntry` per line (JSONL format) |
| `save_full_state(state, path)` | Writes `state.json` — full `state.model_dump()` as JSON |
| `Timer` (context manager) | Measures latency in ms: `with Timer() as t: ... t.latency_ms` |

Every agent and scoring function calls `log_step()` during execution, producing a complete execution trace that can be replayed and analyzed.

---

## 14. Evaluation: `eval/`

### `eval/test_cases.py`
7 stress-test cases designed to probe different capabilities (tc1-tc4 original; tc5-tc7 added later — see eval/test_cases.py for what each stresses):

| ID | Name | Query | Stress test |
|----|------|-------|-------------|
| `tc1_multi_source` | Multi-source synthesis | Compare GPT-4/Llama/Mistral architectures | synthesis |
| `tc2_contradictory` | Contradiction detection | CoT evolution disagreements | contradiction_detection |
| `tc3_sparse` | Sparse evidence | BM25 vs dense retrieval | nuanced_reasoning |
| `tc4_factual` | Factual baseline | Transformer architecture basics | baseline_confidence |

### `eval/harness.py`
```python
run_eval(config, output_prefix, use_mock, output_base) → dict
```
Runs all `TEST_CASES` through `run_research()`, computes metrics per case, saves `eval_summary.json`. Returns a dict keyed by `test_case_id` with metrics, query, stress_test, `adaptive_enabled`, and `verification_enabled`.

### `eval/metrics.py`
**`Metrics`** dataclass with 4 core metrics (mapped to the PDF brief requirements):

| Metric | What it measures | How it's computed |
|--------|-----------------|-------------------|
| `claim_support_rate` | % of claims supported by evidence | `support_score >= support_threshold` |
| `calibration_error` (ECE) | Confidence calibration | Binned (10 bins) Expected Calibration Error; `support_score >= 0.5` as proxy ground truth (decision D008) |
| `contradiction_count` | Cross-source disagreements | Count of `Contradiction` objects |
| `total_tokens` | Compute cost | Sum of all LLM call tokens |

Also tracks: `total_claims`, `supported_claims`, `total_latency_ms`, `total_evidence`, `avg_confidence`, `avg_difficulty`, `total_rounds`.

```python
compute_metrics(state, support_threshold=0.4, num_bins=10) → Metrics
```

### `eval/visualize.py`
Generates the two "wow moment" figures:

| Function | Figure | Track |
|----------|--------|-------|
| `plot_cost_quality(adaptive_results, uniform_results, output_path)` | Scatter: tokens vs claim_support_rate for adaptive vs uniform, with mean markers and improvement arrow | Track A |
| `plot_reliability_diagram(results, output_path, num_bins=10)` | Binned reliability diagram (predicted confidence vs actual accuracy) + confidence histogram | Track B |

Uses matplotlib Agg backend (no display needed).

---

## 15. Ablations: `ablations/`

### `ablations/run_ablation.py`
```python
run_ablation(use_mock, config) → None
```
Runs 3 ablation modes across all test cases:

| Mode | Config change | What it proves |
|------|---------------|----------------|
| `adaptive` | `adaptive.enabled=True` | Track A: adaptive compute |
| `uniform` | `adaptive.enabled=False` | Baseline: all SQs get max budget |
| `no_verify` | `verification.enabled=False` | Track B: verifier is load-bearing |

**Outputs:**
- `ablations/results/cost_quality_curve.png` — Track A wow figure
- `ablations/results/reliability_diagram.png` — Track B wow figure
- `ablations/results/ablation_summary.json` — full metrics table
- Console: comparison table

**Key results** (from `ablation_summary.json`):
- **Adaptive:** 38-55 claims, 25-35K tokens, 92-100% support rate — **98% quality at 3.5x lower cost**
- **Uniform:** 130-195 claims, 97-126K tokens, 98-100% support — more compute, marginal quality gain
- **No-verify:** 0% support rate, ECE meaningless — **verifier is load-bearing** (decision D014)

Entry: `python -m ablations.run_ablation [--mock]`

---

## 16. Outputs & Artifacts

Each run produces a directory (e.g. `outputs/demo_real/`) with:

| File | Contents |
|------|----------|
| `report.md` | The final markdown research report (Executive Summary, Findings, Contradictions, Known Gaps) |
| `state.json` | Full `ResearchState` — all evidence, claims, contradictions, plan, trace, tokens, latency |
| `trace.jsonl` | One `TraceEntry` per line — step-by-step execution trace with timing and token costs |

These are gitignored (see `.gitignore`), along with `ablations/results/` and `*.png`.

Sample `report.md` sections (from `outputs/demo_real/report.md`):
- **Executive Summary** — high-level answer
- **Findings** — per sub-question, with source-cited claims
- **Contradictions & Disagreements** — cross-source conflicts
- **Known Gaps & Limitations** — explicitly stated insufficient evidence

---

## 17. Dependency Graph

```
run.py
  ├── src.orchestrator.config    (Config.load)
  ├── src.orchestrator.pipeline  (run_research)
  │     ├── src.orchestrator.state      (ResearchState)
  │     ├── src.orchestrator.allocator  (allocate_budget, should_continue)
  │     ├── src.agents.planner          (plan)
  │     ├── src.agents.researcher       (research_sub_question)
  │     │     ├── src.tools.arxiv       (search_arxiv, fetch_arxiv_content)
  │     │     │     └── src.tools.pdf   (fetch_pdf_text)
  │     │     ├── src.tools.search      (web_search)
  │     │     └── src.retrieval.chunker (chunk_text)
  │     ├── src.agents.extractor        (extract_claims)
  │     ├── src.scoring.verifier        (verify_claims, detect_contradictions)
  │     ├── src.scoring.confidence      (score_confidence)
  │     ├── src.scoring.difficulty      (estimate_difficulty, update_difficulty)
  │     ├── src.agents.synthesizer      (synthesize)
  │     ├── src.tools.base              (LLMClient)
  │     ├── src.tools.mock_llm          (MockLLMClient)
  │     └── src.obs.trace               (log_step, save_trace, save_full_state)
  │
  ├── eval.harness               (run_eval — when --eval)
  │     ├── eval.test_cases      (TEST_CASES)
  │     ├── eval.metrics         (compute_metrics)
  │     └── src.orchestrator.pipeline (run_research)
  │
  └── ablations.run_ablation     (run_ablation — when --ablation)
        ├── eval.harness
        └── eval.visualize       (plot_cost_quality, plot_reliability_diagram)
```

**Key dependency notes:**
- `pipeline.py` is the hub — it imports from every other module.
- `researcher.py` is the only agent that imports retrieval tools (`arxiv`, `search`, `pdf`, `chunker`).
- `difficulty.py` reads confidence scores (from `verifier.py` via `confidence.py`) — this is the Track A↔B coupling.
- `synthesizer.py` is the only agent using `synthesis_model`; all others use `sub_step_model`.
- `mock_llm.py` is interchangeable with `base.py`'s `LLMClient` — same interface.

---

## 18. How to Run

### Install
```bash
pip install -e .
```

### Set up environment
Create a `.env` file (auto-loaded by `run.py`):
```bash
# For OpenAI (configs/default.yaml)
OPENAI_API_KEY=sk-...

# For OpenRouter (configs/openrouter.yaml)
OPENROUTER_API_KEY=sk-or-...

# Web search backend (optional, default: auto)
SEARCH_BACKEND=auto    # or: tavily, duckduckgo, wikipedia
TAVILY_API_KEY=tvly-... # only if SEARCH_BACKEND=tavily
```

### Modes
```bash
# Single query
python run.py "How do Transformers compare to RNNs?"

# Demo (built-in query)
python run.py --demo

# Evaluation (4 test cases)
python run.py --eval

# Ablation study (adaptive vs uniform vs no-verify)
python run.py --ablation

# Mock mode (no API key needed — for testing/CI)
python run.py --mock --demo

# OpenRouter free model
python run.py --openrouter --demo

# Custom config
python run.py --config configs/openrouter.yaml --demo
```

### Outputs
Results are written to `outputs/<name>/` (for demo/eval) or `ablations/results/` (for ablation):
- `report.md` — the research report
- `state.json` — full pipeline state
- `trace.jsonl` — execution trace

---

## 19. Design Decisions Summary

From `DECISIONS.md` — 14 numbered decisions (D001-D014):

| ID | Decision | Rationale |
|----|----------|-----------|
| D001 | Pydantic for state | Validation, JSON serialization, type safety |
| D002 | Two LLM models (sub-step vs synthesis) | Stronger model for final report, cheaper for intermediate steps |
| D003 | arXiv primary, web secondary | Academic rigor; web fills gaps |
| D004 | Track A+B hybrid | Mutually reinforcing — confidence feeds difficulty |
| D005 | Two-phase difficulty | Linguistic pre-retrieval + confidence post-retrieval |
| D006 | Verifier uses different model | Avoid self-agreement bias (noted limitation: same model in practice) |
| D007 | Heuristic calibration | Simpler than temperature scaling; no held-out set needed |
| D008 | Proxy ground truth | `support_score >= 0.5` as "correct" for ECE — acknowledged limitation |
| D009 | Fixed-size chunking (1500+200) | Simplicity; overlap prevents sentence splits |
| D010 | Cross-source contradiction only | Same-source claims shouldn't contradict by construction |
| D011 | Uniform mode = max_budget for all | Fair baseline for ablation |
| D012 | Adaptive 3.5x more efficient | **Qualified by D036**: true vs the max-compute baseline, false vs min-compute |
| D013 | System under-confident (ECE=0.37) | **Superseded by D030**: the gap was an arithmetic ceiling (weights summing to 0.8), not model under-confidence |
| D014 | Verifier is load-bearing | Without it, claim-support = 0% |

---

## Acknowledged Limitations

(from `README.md` and `DECISIONS.md`)

1. **Heuristic calibrator compresses confidence** — not a learned calibrator; confidence range is narrow.
2. **arXiv + web only** — no general web crawling or proprietary databases.
3. **Self-agreement bias** — `gpt-4o-mini` used for all steps in practice (D006 limitation).
4. **Proxy ground truth for ECE** — `support_score >= 0.5` is not true ground truth (D008).
5. **Abstracts vs full PDFs** — some runs use abstracts only; PDF fetching can fail.
6. **Free model JSON mode** — deepseek-r1:free doesn't support `response_format: json_object`; regex extraction fallback is used.

---

*This document was generated after merging branch `002-free-model-websearch-pdf` into `master` (commit `766e5d9`). It reflects the full codebase at that commit.*

---

## 20. Design Rationale & Results: Architecture ↔ Research Directions

> This section connects every module in the codebase to the research directions from the "Advanced Areas of Pursuit" section of the project plan. For each direction, we explain **why** we made the design choices we did, **what** those choices produced when we ran the ablations, and **what they did not solve** — the known limitations that remain.
>
> The goal is not to claim that our implementation matches the frontier literature. It does not. The goal is to be explicit about which ideas from the literature we drew on, how we translated them into a working system, what worked, what didn't, and what we learned.

### 20.1 Search-and-Reason Interleaving (ReAct, Search-R1)

**The research direction.** The Notion plan references ReAct (interleaving reasoning steps with tool calls) and Search-R1 (interleaving search actions with chain-of-thought reasoning). The core question: *should the agent search-then-summarize (one shot), or should it think→search→inspect→revise→search again?*

**What we built and why.** The adaptive retrieval loop (§7, §3) is a search-and-reason interleaving architecture. Each sub-question goes through:

1. **Think:** The planner decomposes the query into sub-questions (reasoning about what to search for).
2. **Search:** The researcher retrieves evidence from arXiv + web.
3. **Inspect:** The extractor pulls atomic claims from the evidence; the verifier scores each claim's support.
4. **Revise:** The confidence scorer evaluates whether the evidence is sufficient. If not, the difficulty estimator updates the sub-question's difficulty, the allocator extends the compute budget, and the loop searches again.
5. **Answer:** After all sub-questions are resolved, the synthesizer writes the report.

This is not ReAct in the strict sense — we do not have a single LLM that emits `Thought:` / `Action:` / `Observation:` tokens in a loop. Instead, the interleaving is **orchestrated by the pipeline**, with each step handled by a specialized agent (planner, researcher, extractor, verifier, synthesizer). We made this choice because:

- **Separation of concerns.** Each agent has a single responsibility and a tailored prompt. A monolithic ReAct agent would need a single prompt that handles planning, search, extraction, verification, and synthesis — a prompt engineering nightmare that blurs the reasoning steps.
- **Observability.** With separate agents, each step is a traceable event (§13). We can see exactly what the planner decided, what the researcher retrieved, what the extractor pulled, and what the verifier scored. A monolithic ReAct loop would make it harder to attribute failures to specific reasoning steps.
- **Testability.** Each agent can be tested in isolation. The ablation modes (§15) can disable the verifier without touching the researcher or extractor.

**What it produced.** The interleaving produced a **3.5× cost reduction** with no quality loss. In the adaptive ablation, the system used 25-35K tokens and achieved a 92-100% support rate. In the uniform ablation (all sub-questions get max budget regardless of difficulty), the system used 97-126K tokens and achieved 98-100% support. The interleaving with adaptive stopping captured 98% of the quality at 1/3.5 the cost.

More importantly, the interleaving produced **targeted retrieval**. The trace shows that easy sub-questions (e.g., "What is transformer architecture?") got 1-2 rounds and stopped, while hard sub-questions (e.g., "How do RAG and long-context window approaches compare in cost?") got 3-4 rounds with increasingly specific search queries. A search-then-summarize baseline would spend the same budget on both.

**What it did not solve.** The search queries are generated by the researcher agent's prompt, not by a learned retrieval policy. The system cannot learn from its retrieval mistakes across runs — if a search query returns irrelevant results, the next run with the same query will get the same results. A learned retrieval policy (as in Search-R1) would adapt query generation based on what worked.

The interleaving is also **within-sub-question**, not **across-sub-questions**. If sub-question 1 retrieves evidence that is also relevant to sub-question 3, the system does not carry it forward — sub-question 3 starts its own retrieval from scratch. A cross-sub-question context-sharing mechanism would reduce redundant retrieval.

### 20.2 Process Reward Models: Step-Level Verification

**The research direction.** The Notion plan references Process Reward Models (PRMs) — models that evaluate the quality of intermediate reasoning steps, not just the final answer. The question: *should you evaluate the final report, or should you evaluate each claim as it is extracted?*

**What we built and why.** The verifier (§12.2) is a lightweight process reward model. It evaluates each claim *as it is extracted*, not just the final synthesis. For every claim, the verifier produces:

- `support_score` (0.0-1.0): how well the evidence supports the claim
- `verification_status`: `verified`, `partially_supported`, `contradicted`, `unverified`
- `reasoning`: a text explanation of the score

This is a PRM in the sense that it provides **step-level reward signals** that feed back into the process. The support score flows into the confidence scorer, which flows into the difficulty estimator, which drives compute allocation. A low support score on a claim → low confidence → higher difficulty → more retrieval rounds → more evidence → (hopefully) higher support on the next pass.

We chose per-claim verification over final-report verification because:

- **Granular feedback.** A final-report score tells you the report is bad, but not which claim is the problem. Per-claim verification pinpoints the unsupported claim, and the adaptive loop can target it with more retrieval.
- **Early stopping.** If all claims in a sub-question are well-supported after round 1, there is no need for round 2. A final-report evaluation cannot make this determination until the report is written.
- **Contradiction detection.** Cross-claim contradiction detection (§12.2, `detect_contradictions`) only works if claims are evaluated individually before synthesis. A final-report evaluation would miss the contradiction between claim A (from source X) and claim B (from source Y).

**What it produced.** The no-verify ablation (§15) proved that the verifier is **load-bearing**. With verification disabled, the support rate dropped to 0% — the system extracted claims but had no mechanism to assess whether they were backed by evidence. The ECE became meaningless because there was no confidence signal to calibrate.

With verification enabled, the system achieved 92-100% support rate in adaptive mode. The verifier caught unsupported claims and assigned them low confidence, which the synthesizer then presented as uncertain rather than as facts.

**What it did not solve.** The verifier is an LLM, not a trained reward model. Its support scores are **heuristic judgments**, not calibrated probabilities. A true PRM would be trained on a dataset of (claim, evidence, support) labels and would produce a calibrated probability. Our verifier produces a number that is correlated with support quality but not calibrated — the ECE of 0.37 (§15) shows the downstream confidence is systematically under-confident, which traces back to the verifier's score distribution.

The verifier also uses the same model family as the extractor (decision D006), creating a **self-agreement bias**. If the extractor hallucinates a claim, the verifier (same model family) is more likely to agree that the evidence supports it. A different model family for verification would reduce this bias, but at higher cost.

### 20.3 Self-Correction (SCoRe): Per-Claim Revision Loops

**The research direction.** The Notion plan references SCoRe (Self-Correction via Reinforcement Learning) and a writer→verifier→researcher→writer revision loop. The question: *can the system correct its own mistakes by revisiting and revising its output?*

**What we built and why.** We implemented a **per-claim self-correction loop**, not a report-level revision loop. The adaptive retrieval loop (§7) is the self-correction mechanism:

1. **Write:** The extractor extracts claims from evidence.
2. **Verify:** The verifier scores each claim.
3. **Assess:** The confidence scorer evaluates whether confidence is sufficient.
4. **Correct:** If confidence is low, the system does not *revise the claim* — it *retrieves more evidence* and re-extracts. The new evidence may support the claim better, or it may contradict it (which the contradiction detector catches).

This is a weaker form of self-correction than SCoRe. SCoRe trains the model to revise its own output based on feedback. Our system does not revise claims — it retrieves more evidence and re-extracts from scratch. The difference is:

- **SCoRe:** "This claim is wrong. Let me rewrite it to be more accurate."
- **Our system:** "This claim is not well-supported. Let me find more evidence and extract again."

We chose this approach because:

- **No training required.** SCoRe requires reinforcement learning to teach the model to self-correct. We do not have the training infrastructure or the dataset. Our approach works with off-the-shelf LLMs.
- **Evidence-grounded correction.** SCoRe's self-correction is model-internal — the model revises its own reasoning. Our correction is evidence-grounded — the system retrieves new external evidence. For a research agent, evidence-grounded correction is more appropriate: the system should find better sources, not just rephrase its claims.
- **Avoids hallucination loops.** A model revising its own output without new evidence risks hallucinating a "correction" that is actually worse. Retrieving new evidence anchors the correction in external data.

**What it produced.** The adaptive loop's self-correction produced measurable quality improvements. In the trace data, sub-questions that went through 3-4 rounds showed increasing support scores across rounds — the first round might extract a claim with 0.5 support, and the third round might find additional evidence that raises it to 0.8. The adaptive ablation's 92-100% support rate (vs. 0% without verification) shows that the loop does improve claim quality.

**What it did not solve.** The self-correction is **within-sub-question**, not **report-level**. After synthesis, the system does not re-read the full report and check for internal consistency, logical flow, or completeness. A report-level revision loop (writer→verifier→writer) would catch issues like: "the report says X in section 1 but contradicts X in section 3" or "the report doesn't address the original question's second part."

The self-correction is also **not learned**. The system does not get better at self-correcting across runs. Each run starts from scratch. SCoRe's reinforcement learning approach would improve the model's self-correction ability over time, but requires a training pipeline we did not build.

### 20.4 Test-Time Compute Scaling: Adaptive Budgets

**The research direction.** The Notion plan identifies test-time compute scaling as the "strongest recommendation" — the idea that harder problems should get more compute at inference time. The question: *how do you decide how much compute to allocate, and when to stop?*

**What we built and why.** Track A (§8) implements adaptive test-time compute scaling. The system estimates sub-question difficulty and allocates a proportional compute budget (number of retrieval rounds):

- **Difficulty → Budget:** Linear interpolation. Difficulty 0 → 1 round, difficulty 1 → 4 rounds.
- **When to stop:** Three conditions: (1) budget exhausted, (2) average claim confidence ≥ `low_confidence_threshold` (0.5), or (3) max budget reached.
- **Adaptive extension:** If confidence is low after the initial budget is spent, the system updates difficulty from confidence and extends the budget up to `max_budget`.

The critical design choice is the **difficulty signal**. We use a two-phase estimator (§12.1):
- Phase 1 (pre-retrieval): Linguistic cues — keywords like "compare" or "contradict" signal harder questions; "define" or "explain" signal easier ones.
- Phase 2 (post-retrieval): Confidence-based — `1.0 - avg_claim_confidence`. Low confidence → high difficulty → more compute.

Phase 2 is the closed feedback loop. It means the system does not commit to a budget before seeing any evidence — it adjusts based on what it actually finds. This is the key difference from a fixed-budget system.

We chose this two-phase approach because:

- **No pre-trained difficulty model.** A learned difficulty estimator would require a training dataset of (question, difficulty) pairs. We do not have one. Linguistic cues are a crude but zero-cost proxy.
- **Confidence is a better signal than linguistic features.** The whole point of Track B (verification) is to produce a confidence signal. If that signal is good, it should drive Track A (compute allocation). The 0.6/0.4 weighting (confidence vs. linguistic) reflects that confidence is the primary signal, but linguistic features provide a pre-retrieval starting point.
- **Linear interpolation is the simplest non-trivial mapping.** A non-linear mapping (e.g., exponential) would concentrate compute on the hardest questions but might over-spend. Linear is transparent and easy to reason about.

**What it produced.** The ablation (§15) produced the project's central result: **adaptive compute achieves 98% of the quality of uniform compute at 3.5× lower cost**. The cost-quality curve (`cost_quality_curve.png`) shows adaptive sitting high on the quality axis and low on the cost axis, with uniform far to the right on cost with only marginally higher quality.

The trace data shows the adaptive allocator spending compute where it matters. Easy sub-questions (linguistic difficulty ~0.2) got 1-2 rounds. Hard sub-questions (linguistic difficulty ~0.7) got 3-4 rounds. The uniform mode spent 4 rounds on everything, including easy sub-questions that were fully answered after 1 round.

**What it did not solve.** The difficulty estimator is **heuristic, not learned**. The linguistic keyword matching is crude — a question like "Explain the debate around RAG vs. long-context" would match both "explain" (easy) and "debate" (hard), and the net difficulty depends on the order of keyword matching. A learned difficulty estimator would be more robust.

The stopping criterion is **confidence-threshold-based**, not **marginal-information-gain-based**. The Notion plan asks: "when should it stop searching?" Our answer is "when confidence is high enough." A more principled answer would be "when the marginal information gain from another retrieval round drops below a threshold" — i.e., when another round of search is unlikely to change the answer. We do not measure marginal information gain; we measure absolute confidence. This means the system might stop too early (confidence is high but the answer is wrong) or too late (confidence is low but more retrieval won't help).

The compute budget is measured in **retrieval rounds**, not **tokens**. A round might retrieve 5 chunks or 15 chunks depending on the search results. A token-level budget would be more precise but would require interleaving retrieval with token counting, which adds complexity without clear benefit at this scale.

### 20.5 Multi-Agent Debate and Review: De-scoped

**The research direction.** The Notion plan references MARS (Multi-Agent Review System) and author-reviewer workflows — multiple agents independently researching and then debating or reviewing each other's work. The question: *does independent review catch errors that a single agent misses?*

**What we did not build and why.** We did not implement multi-agent debate. The system has a single research pipeline with specialized agents (planner, researcher, extractor, verifier, synthesizer), not multiple independent research agents that debate.

We de-scoped multi-agent debate because:

- **Cost multiplication.** Running N independent research agents multiplies the token cost by N. The adaptive ablation already uses 25-35K tokens per query. A 3-agent debate would use 75-105K tokens — more than the uniform baseline, which we already showed is 3.5× more expensive for marginal quality gain.
- **The verifier is the reviewer.** The verifier (§12.2) already plays the reviewer role — it independently assesses each claim against its evidence. The author-reviewer dynamic is present, but within a single pipeline (extractor = author, verifier = reviewer), not across independent pipelines.
- **Complexity vs. benefit.** Multi-agent debate adds orchestration complexity (managing N agents, merging their outputs, handling disagreements) for uncertain benefit. The Notion plan notes it as an option, not a requirement. Given the time budget, we prioritized the two tracks (adaptive compute + verification) that the plan identifies as the core focus.

**What it would have produced.** Multi-agent debate would likely catch **different types of errors** than the verifier. The verifier catches *evidence-quality* errors (claim not supported by evidence). Multi-agent debate would catch *reasoning* errors (the planner decomposed the question poorly, or the synthesizer missed a key claim). The ablation does not test this — we cannot say whether debate would have improved quality.

The cost-quality tradeoff is the key question. If 3× cost produces <3% quality improvement (as the adaptive vs. uniform comparison suggests), debate is not worth it. But if debate catches a class of errors that uniform compute does not, it might be worth it. We do not have the data to answer this.

### 20.6 Dynamic Tool Selection: Partial Implementation

**The research direction.** The Notion plan references dynamic tool selection — routing queries to the appropriate tools based on question type. The question: *should every sub-question use the same retrieval tools, or should the system choose?*

**What we built and why.** We implemented a **fixed tool set** (arXiv + web search) for all sub-questions, not a dynamic tool router. Every sub-question goes through the same retrieval pipeline: arXiv search → web search → (optional) PDF fetch.

We chose a fixed tool set because:

- **Domain coverage.** The test queries are technical research questions (RAG vs. long-context, transformer architecture, etc.). arXiv covers the academic literature; web search covers blog posts, documentation, and industry perspectives. This combination covers the evidence space for the test queries.
- **Simplicity.** A dynamic tool router would need to classify each sub-question ("Is this academic? → arXiv. Is this practical? → web. Is this recent? → web with date filter.") and then route accordingly. This is a learned routing policy or a complex rule system. For 4 test queries with 2 retrieval tools, the complexity is not justified.
- **The researcher already chooses.** The researcher agent's prompt asks it to generate search queries for both arXiv and web. The LLM implicitly "selects" tools by generating better queries for one source vs. another based on the sub-question. This is a weak form of dynamic tool selection — the tool set is fixed, but the query generation adapts.

**What it produced.** The fixed tool set produced good coverage for the test queries. The trace shows that academic sub-questions got most of their evidence from arXiv, while practical sub-questions (e.g., "How much does long-context cost?") got more from web search. The LLM's query generation naturally routed to the more productive source.

**What it did not solve.** The system does not have tools for:
- **Code execution.** Sub-questions that require computation (e.g., "How many tokens does a 1M-context window cost at GPT-4o pricing?") cannot be answered precisely — the system retrieves text about pricing but cannot compute the answer.
- **Database query.** Sub-questions that require structured data (e.g., "How many papers on RAG were published in 2024?") cannot be answered with text retrieval alone.
- **Date-filtered search.** The web search does not support date ranges. Sub-questions about recent developments ("What's new in long-context in 2024?") rely on whatever the search engine returns, which may be outdated.

A dynamic tool router that adds code execution, database query, and date-filtered search would handle these sub-questions better. But it would also add failure modes (code execution errors, database connection failures) that the current system does not have.

### 20.7 Context Management

**The research direction.** The Notion plan lists "Context Management?" with a question mark — suggesting it as an open question rather than a concrete direction. The question: *how do you manage the context window as evidence accumulates across multiple retrieval rounds?*

**What we built and why.** Context management is handled through three mechanisms:

1. **Chunking (§11).** Evidence texts are chunked into 600-character segments. This prevents any single evidence item from consuming the context window. The verifier and extractor see individual chunks, not full documents.
2. **Pydantic state (§6).** All state is stored in typed Pydantic models, not in the LLM's context window. The LLM sees only the inputs for the current step (e.g., the extractor sees evidence chunks; it does not see the planner's output or the verifier's scores). This means the context window is scoped to each agent's inputs, not the entire pipeline state.
3. **Evidence context building (§12.2).** The verifier's `_build_evidence_context()` function caps evidence at 600 characters per chunk and includes only chunks relevant to the current claim. This prevents the verifier's context from growing unboundedly as evidence accumulates.

We chose this approach because:

- **The context window is not the bottleneck.** With gpt-4o-mini (128K context) and 1-4 retrieval rounds producing 5-15 chunks per round, the total evidence per sub-question is well within the context window. Context management becomes critical at scale (100+ rounds, thousands of chunks), which is not our use case.
- **Separation of state from context.** By keeping all state in Pydantic models and giving each agent only its required inputs, we avoid the problem of the LLM's context window filling up with irrelevant state. The orchestrator manages what each agent sees.

**What it produced.** No context window overflow errors in any ablation run. The trace shows each agent receiving a focused context: the extractor sees evidence chunks, the verifier sees a claim + its evidence chunks, the synthesizer sees verified claims + their confidence scores. No agent sees the full pipeline state.

**What it did not solve.** The system does not implement **context compression** — if evidence accumulates beyond what fits in the context window, the system does not summarize or compress earlier evidence. It simply does not include it. The 600-char chunk cap means some evidence is truncated. A context compression mechanism (e.g., summarizing earlier rounds before passing to later rounds) would preserve more information.

The system also does not implement **cross-sub-question context sharing**. If sub-question 1 retrieves a paper that is also relevant to sub-question 3, sub-question 3 does not see it. Each sub-question's evidence is independent. A shared evidence pool would reduce redundant retrieval and might improve coverage.

### 20.8 Infrastructure & Observability: Trace System

**The research direction.** The Notion plan references trace viewers, step-level traces, token and latency accounting, tool-call logs, and deterministic replay. The question: *can you see exactly what the system did, step by step, and replay it?*

**What we built and why.** The trace system (§13) records every step of every pipeline run. Each `log_step` call writes a `TraceEntry` to a JSONL file with:

- `run_id`: unique run identifier
- `step_id`: hierarchical step identifier (e.g., `sq_0.round_1.verify_claim`)
- `tool`: which tool/agent was called
- `inputs`: what was passed to the tool
- `outputs`: what the tool returned
- `token_cost`: tokens consumed by this step
- `latency_ms`: wall-clock time
- `timestamp`: when the step started

At the end of each run, the full `ResearchState` is saved to `state.json`. This enables deterministic replay — load the state, recompute metrics, or re-run from a specific step.

We built this because:

- **The brief requires it.** The Notion plan explicitly calls for "run IDs, step-level traces, token and latency accounting, tool-call logs." This is not optional.
- **Debugging ablations.** When the no-verify ablation produced 0% support rate, the trace showed exactly why — without verification, claims had no support scores, so the confidence scorer assigned 0.3 to everything, and the synthesizer presented everything as uncertain. Without the trace, this would have been a mystery.
- **Attribution.** The trace attributes each token of cost to a specific step. This makes the cost-quality curve (§15) auditable — you can see exactly where the 25-35K tokens went in adaptive mode vs. where the 97-126K tokens went in uniform mode.

**What it produced.** The trace system made debugging straightforward. Every ablation result can be traced back to specific steps. The cost-quality data is not a black box — it is the sum of step-level costs visible in the trace. The `state.json` files enable replay: load a state, inspect it, or recompute metrics with different scoring parameters.

**What it did not solve.** No real-time trace viewer UI. The trace is a JSONL file, not an interactive visualization. For debugging, `cat trace.jsonl | jq` is sufficient. For presentation, the trace can be rendered as a table. A trace viewer (like LangSmith or Phoenix) would be a UI exercise, not a research contribution.

No human approval checkpoints. The pipeline runs fully autonomously — no step pauses for human review. This is appropriate for an ablation study but not for production, where a human might want to approve the planner's decomposition before retrieval begins.

No failure categorization. Errors are logged but not classified. A production system would categorize failures (retrieval failure, extraction failure, verification failure, synthesis failure) to enable targeted debugging.

### 20.9 Testing Directions: What Was Built and What Wasn't

**The research direction.** The Notion plan lists two testing directions:
1. **Result Evaluation:** Measure answer quality, cost, and calibration.
2. **Noisy Retrieval / Imperfect Sources:** Inject bad sources and measure degradation.

**What we built.** Result evaluation is fully implemented (§14). The eval harness measures:
- **Support rate:** fraction of claims with `support_score ≥ 0.4` (proxy for answer quality)
- **Token cost:** total tokens consumed
- **ECE (Expected Calibration Error):** how well confidence correlates with correctness (using proxy ground truth)
- **Cost-quality curve:** the Track A wow figure
- **Reliability diagram:** the Track B wow figure

The ablation (§15) runs three modes (adaptive, uniform, no-verify) across four test queries and produces all metrics.

**What we did not build.** Noisy retrieval testing is not implemented (decision D019). The current test cases use clean retrieval — real arXiv and web results. We did not build a mock retrieval tool that injects outdated, irrelevant, or contradictory sources.

**Why.** Time constraints. The noisy-retrieval experiment is valuable — the Notion plan calls it "a particularly good angle because it produces convincing failure cases without requiring expensive model training." But it requires a separate test harness: a mock retrieval tool that injects controlled bad sources, and a comparison of support-rate and ECE under clean vs. noisy conditions.

**What it would test.** The verifier is the defense against bad sources. The noisy-retrieval experiment would show whether the verifier actually catches bad sources, or whether it rubber-stamps any claim with a citation. The ablation shows the verifier is load-bearing in *clean* conditions (0% support without it). The noisy-retrieval test would show whether it is *robust* in *adversarial* conditions.

**How to implement.** Add a `mock_noisy_retrieval.py` tool that returns a mix of real and bad sources (e.g., 70% real arXiv results + 30% irrelevant or outdated results). Run the eval harness with this tool and compare support-rate and ECE against clean retrieval. The expected result: support rate drops (some claims are backed by bad sources), but the verifier should catch the worst cases and assign low confidence.

### 20.10 Cross-Track Questions: How the Architecture Answers Them

The Notion plan poses several cross-track questions that span both Track A (adaptive compute) and Track B (verification). Here is how our architecture answers each:

**Q: "When should it stop searching?"**

The system stops searching a sub-question when one of three conditions is met:
1. The compute budget is exhausted (difficulty-based allocation).
2. Average claim confidence ≥ `low_confidence_threshold` (0.5).
3. The max budget (4 rounds) is reached.

This is a **confidence-based stopping criterion**, not a **marginal-information-gain** criterion. It answers "is the evidence sufficient?" but not "would more searching change the answer?" The limitation is that the system might stop too early (confidence is high but the answer is wrong) or too late (confidence is low but more retrieval won't help). A marginal-information-gain approach would track whether new evidence is changing the claims; if not, stop. We do not implement this.

**Q: "How do you prevent the system from converging on the wrong answer?"**

Three mechanisms:
1. **The verifier** independently assesses each claim. If the evidence does not support the claim, the verifier assigns a low support score, which flows into low confidence, which triggers more retrieval (self-correction loop).
2. **The contradiction detector** flags claims from different sources that contradict each other. This prevents the system from presenting a one-sided view — if source X says A and source Y says ¬A, the system presents both and notes the contradiction.
3. **The confidence scorer** penalizes contradictions (0.3 penalty) and unverified claims (0.3 flat). This means the synthesizer presents contradicted or unverified claims as uncertain, not as facts.

The limitation is **self-agreement bias** (decision D006). If the extractor and verifier use the same model family, the verifier is more likely to agree with the extractor's claims — even if they are wrong. A different model family for verification would reduce this, but we use the same model for cost reasons.

**Q: "What's the marginal information gain of one more search?"**

We do not measure this directly. The system measures **absolute confidence** (is the current evidence sufficient?) but not **marginal information gain** (would one more search improve the answer?). The adaptive loop's extension mechanism (if confidence < threshold, extend budget) is a proxy — it assumes that if confidence is low, more searching will help. But this is not always true: if the question is inherently unanswerable with available sources, more searching will not raise confidence, and the system will spend the full budget without improvement.

A marginal-information-gain approach would compare the claims extracted in round N with those extracted in round N-1. If the new claims are largely redundant, the marginal gain is low and the system should stop. We do not implement this comparison.

**Q: "How do you measure whether the system is well-calibrated?"**

The ECE (Expected Calibration Error) metric (§14) measures calibration. We use proxy ground truth — the verifier's support score as a proxy for correctness — and compute ECE as the weighted average of |confidence - accuracy| across confidence bins.

The result: ECE = 0.37, indicating the system is **under-confident** — it assigns lower confidence than the evidence warrants. This traces back to the heuristic confidence formula (§12.3), which compresses the confidence range. A claim with support=0.8 and 2 sources gets confidence = (0.5×0.8 + 0.3×0.75) × 1.0 = 0.625 — even though the support is 0.8. The formula's 0.5/0.3 weighting and diversity multiplier compress the range toward the middle.

The reliability diagram (§15) visualizes this: the confidence predictions cluster in the 0.4-0.7 range, while the actual accuracy (proxy) spans 0.0-1.0. A well-calibrated system would have confidence predictions matching the accuracy line. Our system's predictions are compressed — it rarely says "I'm 95% confident" even when the evidence strongly supports the claim.

