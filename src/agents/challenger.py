"""
Challenger — the adversarial critic that attacks existing claims.

This is the mechanism that lets claims EVOLVE rather than accumulate. The
extractor is append-only: it mints new claims from each round's new evidence and
never revisits what it wrote before. So a round-1 claim never meets the round-3
evidence that undercuts it — the refuting evidence just becomes another peer
claim sitting alongside the one it contradicts.

The challenger closes that gap. It is shown a claim together with the FULL
evidence pool for its sub-question — including chunks the claim does not cite,
which is the entire point — and asked to argue against it.

Two things distinguish it from the verifier:

1. It asks a different question. The verifier asks "does the cited evidence say
   this?" (entailment). The challenger asks "is this claim WARRANTED by the
   evidence as a whole, and does anything in the pool undercut it?" (reasoning
   soundness). A claim can be a faithful restatement of one cherry-picked chunk
   — support_score 0.9 — while being an unsound inference from the full pool.
   `reasoning_score` is that second, orthogonal axis.

2. It runs on a DIFFERENT model from the one that wrote the claim. A model asked
   to critique its own output tends to ratify it; that self-agreement bias is
   the known weakness of "just ask the model to check itself" (DECISIONS.md
   D006). Pointing the challenger at a different model — optionally a different
   provider entirely — is the structural defense, and setting it back to the
   same model is a controlled way to MEASURE the bias (the `evolution_self`
   ablation).

The challenger does not rewrite claims. It only diagnoses. The reviser
(src/agents/reviser.py) does the rewriting, and the routing between them is a
deterministic function of evidence balance (src/orchestrator/evolution.py) — not
the challenger's mood.

QUOTE-GROUNDED REFUTATION (added after the 2026-08 real-model evaluation, see
TESTING.md). A run against real arXiv/web evidence surfaced a concrete failure:
the challenger flagged "Chain-of-thought prompting assists in diagnosing flawed
conclusions" as refuted because "the majority of evidence focuses on reasoning
and problem-solving without explicitly linking it to diagnosing flaws" — i.e.
it treated evidence that simply never mentioned the claim as evidence AGAINST
it, despite the prompt explicitly saying not to. Prompted instructions alone
were not sufficient.

The fix is structural, not another sentence in the prompt: refuting evidence
must now come with a verbatim quote from the cited chunk (`refuting_evidence`
is a list of {index, quote} objects, not bare indices), and
`_validate_quote_grounding` mechanically checks that quote is actually a
substring of that chunk's text before the index counts toward the refuting
side. A model can still choose to hallucinate a refuting quote, but it can no
longer refute a claim by pointing at evidence that is merely silent on it —
silence has no quote to fabricate against, or the fabricated quote fails the
substring check and gets dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.obs.trace import Timer, log_step
from src.orchestrator.config import Config
from src.orchestrator.state import Claim, EvidenceChunk, ResearchState


# Recognised flaw taxonomy. Free-form flaws are kept too, but naming the common
# failure modes makes the critique aggregable across runs (see eval/metrics.py).
KNOWN_FLAWS = [
    "overgeneralization",     # claim is broader than the evidence licenses
    "unsupported_causality",  # evidence shows correlation, claim asserts cause
    "scope_error",            # true for one dataset/model/regime, stated absolutely
    "cherry_picked",          # cites agreeing evidence, ignores disagreeing evidence
    "stale_evidence",         # superseded by newer evidence in the pool
    "conflates_metrics",      # mixes non-comparable numbers or benchmarks
    "vague",                  # too imprecise to verify
]


CHALLENGER_SYSTEM = """You are an adversarial reviewer. Another model extracted the claim below from research evidence. Your job is to find what is WRONG with it — not to agree with it.

You are shown the FULL evidence pool for this sub-question, including evidence the claim does not cite. Read all of it before judging.

Assess two SEPARATE things:

1. REASONING SOUNDNESS — is this claim a warranted inference from the evidence?
   A claim can accurately restate one piece of evidence and still be unsound:
   overgeneralizing from a single benchmark, asserting causality from a
   correlation, or ignoring evidence that qualifies it. Judge the inference,
   not just the wording.

2. EVIDENCE BALANCE — list which evidence SUPPORTS the claim and which
   REFUTES it, by index.

   CRITICAL RULE FOR REFUTING EVIDENCE: evidence only counts as refuting if it
   makes a claim that is IN TENSION with this specific claim, about the SAME
   metric or aspect. For every piece of refuting evidence, you must supply a
   short VERBATIM QUOTE from that exact evidence chunk that states the
   contradiction — copy the words, do not paraphrase. If you cannot find such
   a quote, that evidence is NOT refuting, no matter how related the topic is.
   Evidence that is merely silent on this claim, or that discusses a
   DIFFERENT metric/capability (e.g. "cannot generalize to open-ended tasks"
   does not refute a claim specifically about "instruction-following
   accuracy" — those are different things), is NOT refuting evidence. Name
   the specific dimension in dispute in `contested_dimension`.

Flaw vocabulary (use these labels where they fit, add your own if none do):
overgeneralization, unsupported_causality, scope_error, cherry_picked,
stale_evidence, conflates_metrics, vague

3. REVISION HISTORY. If this claim has been challenged before, its previous
   wordings are shown. Read them. If the change you are about to argue for
   would return the claim to a wording it ALREADY HELD and was moved away
   from, do not argue for it — that is a sign the evidence genuinely does not
   settle this question, and cycling between two wordings adds nothing. Set
   "contested_stalemate": true and explain what the two sides are. A claim
   the evidence cannot decide is a real finding; a claim that flip-flops
   forever is noise.

Verdicts:
- "sound": the claim is warranted as written
- "needs_nuance": basically right, but evidence requires a scope qualifier
- "needs_reversal": the weight of evidence points the other way
- "unsupported": the evidence does not license this claim at all

Respond as JSON: {
    "reasoning_score": float (0.0 = unsound inference, 1.0 = fully warranted),
    "flaws": ["label", ...],
    "contested_dimension": "the specific metric/aspect in dispute, or empty if none",
    "supporting_evidence_indices": [int, ...],
    "refuting_evidence": [{"index": int, "quote": "verbatim text from that evidence chunk"}, ...],
    "verdict": "sound" | "needs_nuance" | "needs_reversal" | "unsupported",
    "contested_stalemate": true | false,
    "critique": "the single strongest reason this claim is not warranted, one or two sentences"
}

Do not be agreeable. If the claim is genuinely sound, say so — but look hard first."""


@dataclass
class ChallengeResult:
    """The challenger's diagnosis of one claim."""
    reasoning_score: float = 0.5
    flaws: list[str] = field(default_factory=list)
    supporting_evidence_ids: list[str] = field(default_factory=list)
    refuting_evidence_ids: list[str] = field(default_factory=list)
    verdict: str = "sound"
    critique: str = ""
    n_supporting_sources: int = 0
    n_refuting_sources: int = 0
    evidence_balance: float = 0.0
    challenger_model: str = ""
    contested_dimension: str = ""
    refuting_quotes: list[str] = field(default_factory=list)
    dropped_ungrounded_refutations: int = 0
    contested_stalemate: bool = False   # evidence cannot settle this; stop cycling


def _format_evidence_pool(pool: list[EvidenceChunk], cited_ids: set[str]) -> str:
    """
    Render the evidence pool for the challenger, marking which chunks the claim
    actually cites. The uncited chunks are what make revision possible — they
    are usually where the refuting evidence lives.
    """
    lines = []
    for i, chunk in enumerate(pool):
        marker = "CITED BY CLAIM" if chunk.chunk_id in cited_ids else "not cited"
        lines.append(
            f"\n--- Evidence [{i}] (source: {chunk.source_title}, "
            f"type: {chunk.source_type}, round: {chunk.retrieval_round}, {marker}) ---\n"
            f"{chunk.text[:700]}"
        )
    return "\n".join(lines)


def _format_revision_history(claim: Claim, config: Config) -> str:
    """
    Show the challenger what it (or a previous pass) already did to this claim.

    This exists because of a measured failure, not a hunch. The frozen-pool
    experiment (DECISIONS.md D028) re-challenged claims against evidence that
    never changed and found the loop never settles: 6 of 12 claims cycled
    between wordings they had already held, at a flat ~50% keep rate over five
    passes. The suspected cause is that the challenger is stateless across
    passes — it re-derives its objection from scratch every time, so nothing
    stops it from arguing a claim back to a wording it was moved away from two
    passes ago.

    Giving it the history tests that hypothesis directly: if oscillation is a
    memory problem, it should drop; if the evidence genuinely does not settle
    the question, the challenger now has a way to SAY so (contested_stalemate)
    instead of expressing it as an infinite flip-flop.
    """
    if not config.evolution.challenger_sees_history or not claim.revisions:
        return ""

    recent = claim.revisions[-config.evolution.max_history_entries:]
    lines = [
        f"\nREVISION HISTORY — this claim has already been challenged and changed "
        f"{len(claim.revisions)} time(s):"
    ]
    if claim.original_text:
        lines.append(f"  original: \"{claim.original_text[:200]}\"")
    for r in recent:
        lines.append(
            f"  -> [{r.operation}] became: \"{r.new_text[:200] or '(retracted)'}\"\n"
            f"     because: {r.rationale[:160]}"
        )
    lines.append(
        "  If your objection would move this claim back toward a wording it "
        "already held, set contested_stalemate instead."
    )
    return "\n".join(lines) + "\n"


def _distinct_sources(chunk_ids: list[str], pool_map: dict[str, EvidenceChunk]) -> set[str]:
    """
    Count evidence by DISTINCT SOURCE, not by chunk.

    This matters: a single paper chopped into eight chunks would otherwise
    outvote three independent papers that disagree with it. Source-weighting is
    what makes the balance score mean "how much of the literature agrees" rather
    than "how much text agrees".
    """
    sources = set()
    for cid in chunk_ids:
        chunk = pool_map.get(cid)
        if chunk:
            sources.add(chunk.source_title)
    return sources


def _normalize(text: str) -> str:
    """Whitespace/case normalization so quote matching tolerates reformatting."""
    return " ".join(text.lower().split())


def _validate_quote_grounding(
    refuting_claims: list[dict],
    evidence_pool: list[EvidenceChunk],
) -> tuple[list[int], list[str], int]:
    """
    Keep only refuting claims whose quote is actually a substring of the chunk
    they cite. This is the mechanical enforcement of "silence is not
    refutation": a model can still hallucinate a quote, but it can no longer
    refute a claim by pointing at evidence that never discusses it, because
    there is no real text to (accidentally or dishonestly) quote.

    Returns (kept_indices, kept_quotes, n_dropped).
    """
    kept_indices, kept_quotes = [], []
    dropped = 0
    for entry in refuting_claims:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("index")
        quote = str(entry.get("quote", "") or "").strip()
        if not isinstance(idx, int) or not (0 <= idx < len(evidence_pool)) or not quote:
            dropped += 1
            continue
        chunk_text = _normalize(evidence_pool[idx].text)
        if _normalize(quote) in chunk_text:
            kept_indices.append(idx)
            kept_quotes.append(quote)
        else:
            dropped += 1
    return kept_indices, kept_quotes, dropped


def compute_evidence_balance(n_supporting: int, n_refuting: int) -> float:
    """
    Source-weighted balance in [-1, +1]:
      +1.0 → every source that speaks to the claim supports it
       0.0 → the literature is evenly split
      -1.0 → every source that speaks to the claim refutes it

    This is the quantity the evolution router thresholds on, so that "the
    contradicting evidence has become dominant, flip the claim" is an arithmetic
    decision that shows up in the trace — not a judgement call buried in an LLM
    response.
    """
    total = n_supporting + n_refuting
    if total == 0:
        return 0.0
    return (n_supporting - n_refuting) / total


def challenge_claim(
    state: ResearchState,
    claim: Claim,
    evidence_pool: list[EvidenceChunk],
    llm,
    config: Config,
    round_num: int = 0,
) -> ChallengeResult:
    """
    Run one adversarial challenge against a claim over its sub-question's full
    evidence pool. Returns the diagnosis; does not mutate the claim's text.
    """
    if not evidence_pool:
        return ChallengeResult(verdict="sound", reasoning_score=0.5,
                               critique="No evidence pool available to challenge against.")

    pool_map = {c.chunk_id: c for c in evidence_pool}
    cited_ids = set(claim.evidence_ids)
    evidence_text = _format_evidence_pool(evidence_pool, cited_ids)

    with Timer() as timer:
        result, resp = llm.complete_json(
            system=CHALLENGER_SYSTEM,
            user=(
                f"Claim (version {claim.version}): {claim.text}\n"
                f"{_format_revision_history(claim, config)}\n"
                f"Full evidence pool for this sub-question:\n{evidence_text}\n\n"
                f"Attack this claim. What does the evidence as a whole actually license?"
            ),
        )

    def _indices_to_ids(key: str) -> list[str]:
        ids = []
        for idx in result.get(key, []) or []:
            if isinstance(idx, int) and 0 <= idx < len(evidence_pool):
                ids.append(evidence_pool[idx].chunk_id)
        return ids

    supporting_ids = _indices_to_ids("supporting_evidence_indices")

    # Refuting evidence must be quote-grounded (see module docstring). Accept
    # the old bare-index shape too (mock LLM, or a model that ignores the
    # schema) but treat it as ungrounded — no quote to check means it doesn't
    # count, consistent with "silence is not refutation".
    raw_refuting = result.get("refuting_evidence", None)
    if raw_refuting is None:
        raw_refuting = [
            {"index": idx, "quote": ""}
            for idx in (result.get("refuting_evidence_indices") or [])
        ]
    refuting_indices, refuting_quotes, dropped = _validate_quote_grounding(
        raw_refuting, evidence_pool
    )
    refuting_ids = [evidence_pool[i].chunk_id for i in refuting_indices]

    sup_sources = _distinct_sources(supporting_ids, pool_map)
    ref_sources = _distinct_sources(refuting_ids, pool_map)
    # A source that both supports and refutes is genuinely mixed — count it on
    # the refuting side, since internal disagreement undercuts a flat assertion.
    sup_sources -= ref_sources

    balance = compute_evidence_balance(len(sup_sources), len(ref_sources))

    try:
        reasoning_score = float(result.get("reasoning_score", 0.5))
    except (TypeError, ValueError):
        reasoning_score = 0.5
    reasoning_score = max(0.0, min(1.0, reasoning_score))

    verdict = result.get("verdict", "sound")
    if verdict not in ("sound", "needs_nuance", "needs_reversal", "unsupported"):
        verdict = "sound"

    flaws = [str(f) for f in (result.get("flaws") or []) if f]

    challenge = ChallengeResult(
        reasoning_score=reasoning_score,
        flaws=flaws,
        supporting_evidence_ids=supporting_ids,
        refuting_evidence_ids=refuting_ids,
        verdict=verdict,
        critique=str(result.get("critique", ""))[:500],
        n_supporting_sources=len(sup_sources),
        n_refuting_sources=len(ref_sources),
        evidence_balance=balance,
        challenger_model=getattr(llm, "model", "unknown"),
        contested_dimension=str(result.get("contested_dimension", "") or "")[:200],
        refuting_quotes=refuting_quotes,
        dropped_ungrounded_refutations=dropped,
        contested_stalemate=bool(result.get("contested_stalemate", False)),
    )

    log_step(
        state,
        component="challenger",
        step="challenge",
        input_summary=f"Claim v{claim.version}: {claim.text[:80]}",
        output_summary=(
            f"verdict={verdict}, reasoning={reasoning_score:.2f}, "
            f"balance={balance:+.2f} ({len(sup_sources)} for / {len(ref_sources)} against)"
            + (f", dropped {dropped} ungrounded refutation(s)" if dropped else "")
        ),
        latency_ms=timer.ms,
        cost_tokens=resp.total_tokens,
        metadata={
            "claim_id": claim.claim_id,
            "claim_version": claim.version,
            "round": round_num,
            "verdict": verdict,
            "reasoning_score": reasoning_score,
            "evidence_balance": balance,
            "flaws": flaws,
            "challenger_model": challenge.challenger_model,
            "contested_dimension": challenge.contested_dimension,
            "dropped_ungrounded_refutations": dropped,
        },
    )

    return challenge
