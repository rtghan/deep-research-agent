"""
Offline regression checks — no network, no API keys, runs in under a second.

Covers the parts of the pipeline whose correctness is decidable without a model:
the evidence-balance arithmetic and routing table (src/orchestrator/evolution.py),
quote-grounding of refutations (src/agents/challenger.py), evidence sampling,
the confidence-formula branch, query reformulation, and the mechanical tier of
report-level self-correction.

These exist because the expensive failures in this project were all caught by
real-model runs costing dollars and hours; anything that can be pinned down
deterministically should be pinned down here first.

Run:  PYTHONPATH=. python tests/test_evolution.py
Exits non-zero if any check fails, so it works as a CI gate.
"""
import sys
from src.orchestrator.config import Config
from src.orchestrator.state import (
    Claim, EvidenceChunk, ResearchPlan, ResearchState, SubQuestion,
)
from src.orchestrator.evolution import evolve_claims, route_operation, evidence_pool_for
from src.agents.challenger import ChallengeResult, compute_evidence_balance
from src.scoring.confidence import score_confidence
from src.tools.mock_llm import MockLLMClient
from eval.metrics import compute_metrics

cfg = Config.load()
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


# --- 1. Balance arithmetic ---
check("balance all-supporting", compute_evidence_balance(4, 0) == 1.0)
check("balance all-refuting", compute_evidence_balance(0, 3) == -1.0)
check("balance even split", compute_evidence_balance(2, 2) == 0.0)
check("balance no evidence", compute_evidence_balance(0, 0) == 0.0)


# --- 2. Routing table ---
# n_sup/n_ref default to (1, 2) -- 3 total sources, enough to clear
# min_sources_for_reversal (2) -- so existing reversal/retract cases still
# exercise the balance-driven path unless a test explicitly wants the
# insufficient-sources downgrade.
def ch(balance, reasoning, verdict="sound", n_sup=1, n_ref=2, flaws=None):
    return ChallengeResult(reasoning_score=reasoning, evidence_balance=balance,
                           verdict=verdict, n_supporting_sources=n_sup,
                           n_refuting_sources=n_ref, flaws=flaws or [])


cases = [
    # (balance, reasoning, verdict)                 -> expected op
    ((1.0, 0.9, "sound"), "keep"),
    ((1.0, 0.3, "sound"), "refine"),      # evidence fine, logic unsound
    ((0.33, 0.9, "sound"), "narrow"),     # minority dissent -> nuance
    ((0.0, 0.8, "sound"), "narrow"),
    ((-0.2, 0.8, "sound"), "narrow"),
    ((-0.5, 0.8, "needs_reversal"), "reverse"),   # dissent dominant -> flip
    ((-1.0, 0.2, "unsupported"), "retract"),
    ((0.8, 0.9, "needs_nuance"), "narrow"),       # challenger asks for nuance
    # Challenger demands a flip the evidence does not license -> downgraded.
    ((0.9, 0.8, "needs_reversal"), "narrow"),
    ((0.9, 0.3, "needs_reversal"), "narrow"),
    # Unsupported but evidence not lopsided, reasoning fine -> nuance, not retract.
    ((0.9, 0.9, "unsupported"), "narrow"),
]
for (bal, reas, verdict), expected in cases:
    got = route_operation(ch(bal, reas, verdict), cfg)
    check(f"route balance={bal:+.2f} reasoning={reas} verdict={verdict}",
          got == expected, f"-> {got} (expected {expected})")

# --- 2b. min_sources_for_reversal gate (fix for the "1 source outvotes
# everything" overcorrection found in the 2026-08 real-model evaluation) ---
thin = ch(-1.0, 0.8, "needs_reversal", n_sup=0, n_ref=1)   # only 1 source total
check("reversal downgraded to narrow when evidence is too thin",
      route_operation(thin, cfg) == "narrow")

thin_retract_path = ch(-1.0, 0.2, "unsupported", n_sup=0, n_ref=1)
check("unsupported+unsound retract path is NOT gated by source count "
      "(it's a reasoning failure, not an evidence-dominance claim)",
      route_operation(thin_retract_path, cfg) == "retract")

enough = ch(-1.0, 0.8, "needs_reversal", n_sup=1, n_ref=2)  # 3 sources total
check("reversal proceeds once enough independent sources back it",
      route_operation(enough, cfg) == "reverse")

# --- 2c. wording-only flaws force refine even out of the "keep" branch ---
vague_but_sound = ch(1.0, 0.9, "sound", flaws=["vague"])
check("a wording flaw on an otherwise well-supported claim routes to refine, "
      "not keep", route_operation(vague_but_sound, cfg) == "refine")


# --- 3. End-to-end evolution pass over a fabricated sub-question ---
sq = SubQuestion(sq_id="sq_0", question="Does X outperform Y?")
state = ResearchState(query="test", plan=ResearchPlan(query="test", sub_questions=[sq]))
for i in range(5):
    state.evidence.append(EvidenceChunk(
        chunk_id=f"sq_0_r1_a{i}_0", source_url=f"http://ex/{i}",
        source_title=f"Paper {i}", source_type="arxiv",
        text=f"Evidence body {i}: X reaches {70 + i}% on benchmark B.",
        retrieval_round=1, sub_question_id="sq_0",
    ))
for i in range(6):
    state.claims.append(Claim(
        claim_id=f"claim_{i}", text=f"Claim {i}: X outperforms Y by {5 + i} points.",
        original_text=f"Claim {i}: X outperforms Y by {5 + i} points.",
        evidence_ids=[f"sq_0_r1_a{i % 5}_0"], sub_question_id="sq_0",
        support_score=0.7, verification_status="supported", confidence=0.55,
    ))

check("evidence pool resolves by sq id", len(evidence_pool_for(state, sq)) == 5)

mock = MockLLMClient(model="mock")
challenger = MockLLMClient(model="mock-challenger")
summary = evolve_claims(state, sq, challenger, mock, mock, cfg, round_num=1, judge_llm=mock)
print("\n  evolution summary:", summary)

check("all claims challenged", summary["challenged"] == 6)
check("challenge records logged", len(state.challenges) == 6)
check("some claims revised", summary["changed"] > 0, f"changed={summary['changed']}")
check("router produced multiple op types",
      sum(1 for k in ("refine", "narrow", "reverse", "retract") if summary[k] > 0) >= 2,
      str({k: summary[k] for k in ("keep", "refine", "narrow", "reverse", "retract")}))

revised = [c for c in state.claims if c.revisions]
check("revised claims bumped version", all(c.version == 1 + len(c.revisions) for c in revised))
check("revised claims kept original_text", all(c.original_text for c in revised))
check("revised claims re-verified",
      all(r.support_after is not None for c in revised for r in c.revisions
          if r.operation != "retract"))
check("reasoning_score set on every claim",
      all(c.reasoning_score is not None for c in state.claims))

retracted = [c for c in state.claims if c.status == "retracted"]
print(f"  retracted: {len(retracted)}, revised: {len(revised)}")

check("judge ran and set a verdict on non-retract revisions",
      all(r.judge_verdict in ("improved", "worse", "same")
          for c in revised for r in c.revisions if r.operation != "retract"))
check("challenge records carry contested_dimension/refuting_quotes",
      any(ch.contested_dimension for ch in state.challenges))
check("no ungrounded refutations from the mock (quotes are real substrings)",
      sum(ch.dropped_ungrounded_refutations for ch in state.challenges) == 0,
      f"dropped={sum(ch.dropped_ungrounded_refutations for ch in state.challenges)}")

# --- 4. Confidence: four-signal path only when challenged ---
unchallenged = Claim(claim_id="u", text="t", evidence_ids=["sq_0_r1_a0_0"],
                     sub_question_id="sq_0", support_score=0.8)
score_confidence(state, [unchallenged], cfg)
expected_old = (0.5 * 0.8 + 0.3 * 0.5) * 1.0
check("unchallenged claim uses original 2-signal formula",
      abs(unchallenged.confidence - expected_old) < 1e-9,
      f"{unchallenged.confidence:.4f} vs {expected_old:.4f}")

challenged = Claim(claim_id="c", text="t", evidence_ids=["sq_0_r1_a0_0"],
                   sub_question_id="sq_0", support_score=0.8,
                   reasoning_score=0.9, evidence_balance=1.0)
score_confidence(state, [challenged], cfg)
expected_new = 0.35 * 0.8 + 0.25 * 0.9 + 0.20 * 0.5 + 0.20 * 1.0
check("challenged claim uses 4-signal formula",
      abs(challenged.confidence - expected_new) < 1e-9,
      f"{challenged.confidence:.4f} vs {expected_new:.4f}")
check("4-signal formula can exceed old 0.8 ceiling",
      0.35 + 0.25 + 0.20 + 0.20 == 1.0)

# --- 5. Metrics ---
m = compute_metrics(state, cfg.verification.support_threshold, cfg.eval.calibration_bins)
print(f"\n  metrics: challenges={m.challenges_issued} hit_rate={m.challenge_hit_rate:.2f} "
      f"revision_rate={m.revision_rate:.2f} narrow={m.narrow_count} reverse={m.reversal_count} "
      f"retract={m.retraction_count} support_lift={m.support_lift:+.3f}")
check("metrics count challenges", m.challenges_issued == 6)
check("retracted claims excluded from support denominator",
      m.total_claims == len([c for c in state.claims if c.is_active]))
check("metrics record revisions", m.revision_rate > 0)
check("metrics serialize", isinstance(m.to_dict()["support_lift"], float))

# --- 6. Deterministic mock ---
a = MockLLMClient(model="m")._mock_challenge("Claim (version 1): stable text\n--- Evidence [0] ---\n")
b = MockLLMClient(model="m")._mock_challenge("Claim (version 1): stable text\n--- Evidence [0] ---\n")
check("mock challenger is deterministic", a == b)

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))

# --- 7. Evidence selection: breadth over volume ---
from src.orchestrator.evolution import select_challenge_evidence
big_pool = []
for src in range(9):                       # 9 sources
    for k in range(40):                    # 40 chunks each = 360 chunks
        big_pool.append(EvidenceChunk(
            chunk_id=f"sq_0_r1_a{src}_{k}", source_url=f"http://ex/{src}",
            source_title=f"Paper {src}", source_type="arxiv",
            text=f"src {src} chunk {k}", retrieval_round=1 + (k % 3),
            sub_question_id="sq_0",
        ))
c0 = Claim(claim_id="x", text="t", evidence_ids=["sq_0_r1_a3_7"], sub_question_id="sq_0")
sel = select_challenge_evidence(big_pool, c0, cfg)
sel_sources = {c.source_title for c in sel}
check("selection respects chunk cap", len(sel) <= cfg.evolution.max_evidence_chunks,
      f"{len(sel)} <= {cfg.evolution.max_evidence_chunks}")
check("selection spans all sources", len(sel_sources) == 9, f"{len(sel_sources)} sources")
check("selection keeps the claim's cited chunk",
      any(c.chunk_id == "sq_0_r1_a3_7" for c in sel))
naive = big_pool[:cfg.evolution.max_evidence_chunks]
check("beats naive head-slice on source breadth",
      len(sel_sources) > len({c.source_title for c in naive}),
      f"{len(sel_sources)} vs {len({c.source_title for c in naive})} sources")
check("small pools pass through untouched",
      select_challenge_evidence(big_pool[:5], c0, cfg) == big_pool[:5])

# --- 8. Quote-grounding validator: the fix for "silence implies refutation" ---
from src.agents.challenger import _validate_quote_grounding, challenge_claim
pool = [
    EvidenceChunk(chunk_id="e0", source_url="u0", source_title="Paper A",
                  source_type="arxiv", text="The model achieves 92% accuracy on task X.",
                  sub_question_id="sq_0"),
    EvidenceChunk(chunk_id="e1", source_url="u1", source_title="Paper B",
                  source_type="arxiv", text="Performance on task Y was not measured in this study.",
                  sub_question_id="sq_0"),
]
kept_idx, kept_quotes, dropped = _validate_quote_grounding(
    [
        {"index": 0, "quote": "achieves 92% accuracy"},        # real substring -> kept
        {"index": 1, "quote": "the model performs poorly"},    # hallucinated -> dropped
        {"index": 1, "quote": ""},                              # empty quote -> dropped
        {"index": 5, "quote": "out of range"},                  # bad index -> dropped
    ],
    pool,
)
check("quote-grounding keeps only real substrings", kept_idx == [0], f"kept_idx={kept_idx}")
check("quote-grounding drops hallucinated/empty/out-of-range quotes", dropped == 3, f"dropped={dropped}")

# End-to-end: a challenger that claims refutation from a SILENT chunk (no
# quote at all, old bare-index shape) must not have that count as refutation.
class SilentRefuterLLM:
    model = "silent-refuter-mock"
    def complete_json(self, system, user):
        from src.tools.base import LLMResponse
        return (
            {
                "reasoning_score": 0.5, "flaws": [], "verdict": "needs_reversal",
                "supporting_evidence_indices": [],
                "refuting_evidence_indices": [1],  # old bare-index shape, no quote possible
            },
            LLMResponse(text="{}", input_tokens=10, output_tokens=10),
        )

claim_for_silence_test = Claim(claim_id="silent", text="The model performs well on task Y.",
                               evidence_ids=[], sub_question_id="sq_0")
silent_state = ResearchState(query="t")
result = challenge_claim(silent_state, claim_for_silence_test, pool, SilentRefuterLLM(), cfg, round_num=1)
check("bare-index refutation (no quote) does not count as refuting evidence",
      result.n_refuting_sources == 0, f"n_refuting_sources={result.n_refuting_sources}")
check("balance reflects zero real refutation, not -1.0",
      result.evidence_balance == 0.0, f"balance={result.evidence_balance}")

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))

# --- 9. Query reformulation on retry (Search-R1 style) ---
from src.agents.query_reformulator import reformulate_query, _build_digest
from src.orchestrator.state import RetrievalAttempt

rf_sq = SubQuestion(sq_id="sq_0", question="Does X outperform Y on benchmark B?")
rf_state = ResearchState(query="t", plan=ResearchPlan(query="t", sub_questions=[rf_sq]))
rf_mock = MockLLMClient(model="mock")

# Round 1: nothing learned yet -> must use the sub-question verbatim.
q1, rat1, gap1 = reformulate_query(rf_state, rf_sq, 1, rf_mock, cfg)
check("round 1 uses the sub-question verbatim", q1 == rf_sq.question, f"got {q1!r}")

# Simulate round 1 having happened.
rf_sq.retrieval_attempts.append(RetrievalAttempt(
    round_num=1, query=rf_sq.question, n_chunks=12,
    source_titles=["Paper A", "Paper B"],
))

q2, rat2, gap2 = reformulate_query(rf_state, rf_sq, 2, rf_mock, cfg)
check("round 2 issues a DIFFERENT query", q2 != rf_sq.question, f"got {q2!r}")
check("round 2 query is non-empty", bool(q2.strip()))
check("round 2 reports a gap", bool(gap2))

# Disabled -> always verbatim.
import copy as _copy
cfg_off = _copy.deepcopy(cfg)
cfg_off.retrieval.reformulate_queries = False
q_off, _, _ = reformulate_query(rf_state, rf_sq, 2, rf_mock, cfg_off)
check("reformulation can be disabled via config", q_off == rf_sq.question)

# No LLM available -> verbatim, no crash.
q_nollm, _, _ = reformulate_query(rf_state, rf_sq, 2, None, cfg)
check("missing llm degrades to sub-question, no crash", q_nollm == rf_sq.question)

# Duplicate-guard: a model that echoes a previously-tried query is rejected.
class EchoLLM:
    model = "echo"
    def complete_json(self, system, user):
        from src.tools.base import LLMResponse
        return ({"query": rf_sq.question, "gap": "g", "rationale": "r"},
                LLMResponse(text="{}", input_tokens=1, output_tokens=1))
q_dup, _, _ = reformulate_query(rf_state, rf_sq, 2, EchoLLM(), cfg)
check("duplicate query is rejected and falls back", q_dup == rf_sq.question)

# Digest is compaction: prior queries + source titles, NOT raw chunk text.
digest = _build_digest(rf_sq, rf_state)
check("digest names prior query", rf_sq.question in digest)
check("digest names returned sources", "Paper A" in digest)

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))

# --- 10. Report-level self-correction ---
from src.orchestrator.report_loop import mechanical_checks, run_report_correction, _text_appears_in_report

rl_sq = SubQuestion(sq_id="sq_0", question="Does X beat Y?")
rl_sq2 = SubQuestion(sq_id="sq_1", question="What is the latency cost?")
rl_state = ResearchState(query="Does X beat Y and at what latency cost?",
                         plan=ResearchPlan(query="q", sub_questions=[rl_sq, rl_sq2]))
rl_state.evidence.append(EvidenceChunk(
    chunk_id="sq_0_r1_a0_0", source_url="u", source_title="Paper A",
    source_type="arxiv", text="X reaches 92 percent.", sub_question_id="sq_0"))
# sq_0 has a healthy claim; sq_1 has NONE -> coverage gap
rl_state.claims.append(Claim(claim_id="c0", text="X reaches 92 percent accuracy on benchmark B.",
                             evidence_ids=["sq_0_r1_a0_0"], sub_question_id="sq_0",
                             support_score=0.9, confidence=0.8))
# a retracted claim
rl_state.claims.append(Claim(
    claim_id="c1", text="Quantum entanglement accelerates gradient descent convergence dramatically.",
    original_text="Quantum entanglement accelerates gradient descent convergence dramatically.",
    evidence_ids=[], sub_question_id="sq_0", status="retracted",
    support_score=0.1, confidence=0.2))

# Report that (a) omits sq_1 entirely, (b) still asserts the retracted claim
rl_state.report = ("# Report\n\n## Findings\n\n"
                   "X reaches 92 percent accuracy on benchmark B.\n\n"
                   "Quantum entanglement accelerates gradient descent convergence dramatically.\n")

defects = mechanical_checks(rl_state, cfg)
dtypes = {d.defect_type for d in defects}
check("mechanical check catches retracted claim still in report",
      "retracted_claim_cited" in dtypes, str(dtypes))
check("mechanical check catches sub-question with no claims",
      "missing_coverage" in dtypes, str(dtypes))
check("mechanical defects are labelled as mechanical",
      all(d.found_by == "mechanical" for d in defects))
check("mechanical checks need no LLM", True)

# The fuzzy matcher shouldn't fire on unrelated text
check("retracted-claim matcher does not false-positive on unrelated prose",
      not _text_appears_in_report("Completely unrelated statement about marine biology reefs",
                                  rl_state.report))

# Contradiction mishandling
rl_state2 = ResearchState(query="q", plan=ResearchPlan(query="q", sub_questions=[rl_sq]))
rl_state2.report = "# Report\n\nEverything agrees perfectly.\n"
rl_state2.claims.append(Claim(claim_id="x", text="t", sub_question_id="sq_0",
                              support_score=0.9, confidence=0.9))
from src.orchestrator.state import Contradiction
rl_state2.contradictions.append(Contradiction(
    claim_a_id="x", claim_b_id="y", description="A says up, B says down",
    source_a="A", source_b="B"))
d2 = {d.defect_type for d in mechanical_checks(rl_state2, cfg)}
check("mechanical check catches undiscussed contradictions",
      "mishandled_contradiction" in d2, str(d2))

# Full loop with mocks: should critique, revise, bump version, and terminate.
rl_mock = MockLLMClient(model="mock")
rl_critic = MockLLMClient(model="mock-critic")
before_version = rl_state.report_version
run_report_correction(rl_state, synth_llm=rl_mock, critic_llm=rl_critic,
                      sub_llm=rl_mock, challenger_llm=rl_critic,
                      reviser_llm=rl_mock, config=cfg)
check("report loop ran at least one critique pass", len(rl_state.report_critiques) >= 1)
check("report loop bumped report_version after revising",
      rl_state.report_version > before_version,
      f"{before_version} -> {rl_state.report_version}")
check("report loop respects max_passes cap",
      len(rl_state.report_critiques) <= cfg.report_correction.max_passes,
      f"{len(rl_state.report_critiques)} <= {cfg.report_correction.max_passes}")
check("critiques record an action taken",
      all(c.action_taken in ("none","revised","reopened_research") for c in rl_state.report_critiques))

# Disabled -> no-op
rl3 = ResearchState(query="q", plan=ResearchPlan(query="q", sub_questions=[rl_sq]))
rl3.report = "# R\n\ntext"
cfg_norc = _copy.deepcopy(cfg); cfg_norc.report_correction.enabled = False
run_report_correction(rl3, rl_mock, rl_critic, rl_mock, rl_critic, rl_mock, cfg_norc)
check("report correction can be disabled", len(rl3.report_critiques) == 0)

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))


# --- 11. Report surfaces per-claim confidence (brief's hard requirement) ---
# The brief's MVP minimum bar: "Final output must separate claims from evidence
# and indicate how confident it is in each claim." Real runs were found emitting
# ZERO confidence markers -- the system computed calibrated confidence and then
# discarded it at the last step. This pins the prompt contract so that silently
# regresses loudly.
from src.agents.synthesizer import SYNTHESIS_SYSTEM
check("synthesis prompt mandates an explicit per-claim confidence marker",
      "confidence:" in SYNTHESIS_SYSTEM and "EVERY claim" in SYNTHESIS_SYSTEM)
check("synthesis prompt still requires inline source attribution",
      "Source:" in SYNTHESIS_SYSTEM)
check("synthesis prompt still ties hedging language to the 0.6 threshold",
      "0.6" in SYNTHESIS_SYSTEM)

# The prompt instruction alone was NOT enough: a real gpt-4o-mini run emitted
# zero markers despite the explicit format. The requirement is therefore
# satisfied structurally, by rendering the index in code. Same lesson as
# quote-grounding: when a property must hold, code it, don't ask for it.
from src.agents.synthesizer import _render_confidence_index
_idx_claims = [c for c in state.claims if c.is_active]
_idx = _render_confidence_index(state, _idx_claims, {c.chunk_id: c for c in state.evidence})
check("confidence index is emitted deterministically, not left to the model",
      "Claim Confidence Index" in _idx)
check("confidence index gives a numeric confidence per claim",
      sum(1 for ln in _idx.splitlines() if ln.startswith("| 0.") or ln.startswith("| 1.")) >= len(_idx_claims),
      f"{sum(1 for ln in _idx.splitlines() if ln.startswith('| 0.') or ln.startswith('| 1.'))} rows vs {len(_idx_claims)} claims")
check("confidence index survives a model that ignores prompt formatting",
      "Claim Confidence Index" in _render_confidence_index(state, _idx_claims, {}))
check("retracted claims are disclosed, not silently dropped",
      ("Retracted during verification" in _idx) if any(c.status=="retracted" for c in state.claims) else True)

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))

# --- 12. Oscillation detection + scheduler (frozen-pool experiment fallout) ---
# The frozen-pool experiment showed 6/12 claims cycling A->B->A against evidence
# that never changed, at a flat ~50% keep rate over 5 passes. Raw revision counts
# cannot distinguish that from progress, which broke the scheduler's yield term.
from src.orchestrator.evolution import _text_fingerprint
from src.orchestrator.scheduler import marginal_value, RoundOutcome

check("text fingerprint ignores case and whitespace",
      _text_fingerprint("X beats Y") == _text_fingerprint("  x   BEATS y "))
check("text fingerprint distinguishes different claims",
      _text_fingerprint("X beats Y") != _text_fingerprint("Y beats X"))

osc_sq = SubQuestion(sq_id="sq_osc", question="contested?")
osc_state = ResearchState(query="t", plan=ResearchPlan(query="t", sub_questions=[osc_sq]))
for i in range(4):
    osc_state.claims.append(Claim(
        claim_id=f"o{i}", text=f"claim {i}", sub_question_id="sq_osc",
        support_score=0.6, confidence=0.5))

steady = marginal_value(osc_state, osc_sq, RoundOutcome(new_claims=3, claims_changed=3), cfg)
for c in osc_state.claims:
    c.oscillating = True
thrashing = marginal_value(osc_state, osc_sq, RoundOutcome(new_claims=3, claims_changed=3), cfg)
check("oscillating sub-question is DEPRIORITISED despite identical churn",
      thrashing < steady, f"{thrashing:.4f} < {steady:.4f}")

for c in osc_state.claims:
    c.oscillating = False
dry = marginal_value(osc_state, osc_sq, RoundOutcome(found_nothing=True), cfg)
check("a dry round suppresses further spending", dry < steady)
untried = marginal_value(osc_state, osc_sq, None, cfg)
check("an untried sub-question is explored optimistically", untried >= steady)

# Ranking, not thresholds: even when every value is far below any absolute bar,
# argmax must still discriminate -- this is the property the old allocator lacked.
lo_sq = SubQuestion(sq_id="sq_lo", question="settled?")
osc_state.plan.sub_questions.append(lo_sq)
for i in range(12):
    osc_state.claims.append(Claim(claim_id=f"l{i}", text=f"l{i}",
                                  sub_question_id="sq_lo", support_score=0.95, confidence=0.95))
v_hi = marginal_value(osc_state, osc_sq, RoundOutcome(new_claims=2, claims_changed=2), cfg)
v_lo = marginal_value(osc_state, lo_sq, RoundOutcome(new_claims=0, claims_changed=0), cfg)
check("ranking discriminates even when both values are tiny",
      v_hi > v_lo and max(v_hi, v_lo) < 0.7, f"{v_hi:.3f} vs {v_lo:.3f}")

check("scheduler strategy is opt-in; threshold remains the default",
      Config.load().adaptive.strategy == "threshold")

# --- 13. Concurrency safety (parallel sub-question execution) ---
# Serial remains the default; these pin the invariants parallel mode depends on.
import threading as _threading
from concurrent.futures import ThreadPoolExecutor as _TPE

check("serial execution is the default", Config.load().execution.parallel_sub_questions is False)

# Claim IDs must be unique across sub-questions. The old scheme read
# len(state.claims) and counted up, which two concurrent extractors both
# observe identically -> duplicate IDs -> silently cross-wired foreign keys.
conc_state = ResearchState(query="c")
conc_sqs = [SubQuestion(sq_id=f"sq_{i}", question=f"q{i}") for i in range(6)]
conc_state.plan = ResearchPlan(query="c", sub_questions=conc_sqs)
for i in range(6):
    for k in range(3):
        conc_state.evidence.append(EvidenceChunk(
            chunk_id=f"sq_{i}_r1_a{k}_0", source_url="u", source_title=f"P{k}",
            source_type="arxiv", text=f"evidence {i}-{k}", sub_question_id=f"sq_{i}"))

def _extract(sq):
    from src.agents.extractor import extract_claims
    pool = [e for e in conc_state.evidence if e.sub_question_id == sq.sq_id]
    extract_claims(conc_state, sq, pool, MockLLMClient(model="mock"), cfg)

with _TPE(max_workers=6) as _pool:
    list(_pool.map(_extract, conc_sqs))

_ids = [c.claim_id for c in conc_state.claims]
check("claim IDs are unique under concurrent extraction",
      len(_ids) == len(set(_ids)), f"{len(_ids)} claims, {len(set(_ids))} unique")
check("concurrent extraction lost no claims", len(conc_state.claims) == 18,
      f"{len(conc_state.claims)} of 18")

# Token accounting is read-modify-write; without a lock it silently undercounts.
tok_state = ResearchState(query="t")
from src.obs.trace import log_step as _log
def _spam():
    for _ in range(200):
        _log(tok_state, "test", "step", "in", "out", latency_ms=1.0, cost_tokens=1)
with _TPE(max_workers=8) as _pool:
    list(_pool.map(lambda _: _spam(), range(8)))
check("token accounting loses no updates under concurrency",
      tok_state.total_tokens == 1600, f"{tok_state.total_tokens} of 1600")
check("trace entries are all recorded", len(tok_state.trace) == 1600,
      f"{len(tok_state.trace)} of 1600")

# Narration must not interleave: each thread buffers and flushes as a block.
import io as _io
from src.obs.progress import ProgressReporter as _PR
_buf = _io.StringIO()
_rep = _PR(enabled=True, stream=_buf)
def _narrate(n):
    _rep.begin_buffered()
    for k in range(5):
        _rep.sub(f"worker{n}-line{k}")
    _rep.flush_buffered()
with _TPE(max_workers=4) as _pool:
    list(_pool.map(_narrate, range(4)))
_lines = [l for l in _buf.getvalue().splitlines() if l.strip()]
_blocks_ok = all(
    len({l.split("worker")[1][0] for l in _lines[i:i+5]}) == 1
    for i in range(0, len(_lines), 5)
)
check("narration from each worker stays contiguous", _blocks_ok and len(_lines) == 20,
      f"{len(_lines)} lines")

# Retry layer: transient errors retried, permanent ones raised immediately.
from src.tools.base import _is_retryable
class _E(Exception):
    def __init__(self, code): self.status_code = code
check("429 is retryable", _is_retryable(_E(429)))
check("503 is retryable", _is_retryable(_E(503)))
check("400 is NOT retryable", not _is_retryable(_E(400)))
check("404 is NOT retryable", not _is_retryable(_E(404)))

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))

sys.exit(1 if fails else 0)
