"""
Claim evolution loop — challenge, route, revise, re-verify.

The problem this solves: the extractor is append-only. Each round it reads that
round's new chunks and mints new claims. Nothing ever revisits a claim already
in state. A sub-question that runs three rounds ends up with three batches of
mutually independent claims — so the system can retrieve evidence that flatly
refutes a round-1 claim and the only thing that happens is a second, peer claim
appears next to it. The contradiction detector notices the pair at the very end
and applies a flat confidence penalty to both. Nobody ever says "claim_3 was
wrong; here is the corrected version".

This module makes claims first-class evolving objects. After each retrieval
round, every active claim for the sub-question is challenged against the
sub-question's FULL accumulated evidence pool — not just the chunks it cited,
and not just this round's chunks. That is what lets round-3 evidence rewrite a
round-1 claim.

THE ROUTING IS ARITHMETIC, NOT VIBES
------------------------------------
The decision of whether contradicting evidence means "add a caveat" or "you were
wrong, flip it" is made by thresholding a source-weighted evidence balance:

    balance = (supporting_sources - refuting_sources)
              / (supporting_sources + refuting_sources)

    balance >  0.5, reasoning >= threshold  → keep     (claim stands)
    balance >  0.5, reasoning <  threshold  → refine   (evidence fine, logic bad)
    -0.3 <= balance <= 0.5                 → narrow   (real minority dissent)
    balance <  -0.3                        → reverse  (dissent is now dominant)
    balance <  -0.3 and verdict unsupported → retract

Counting DISTINCT SOURCES rather than chunks is load-bearing: one paper split
into eight chunks must not outvote three papers that disagree with it.

TWO FIXES FROM THE 2026-08 REAL-MODEL EVALUATION:

1. balance is a RATIO, and a ratio is blind to sample size:
   compute_evidence_balance(0, 1) == compute_evidence_balance(0, 10) == -1.0.
   A single dissenting source with nothing else on record for the claim got
   the exact same reversal authority as ten independent papers disagreeing
   with it. `min_sources_for_reversal` gates reverse/retract behind a minimum
   total source count; below it, the claim is downgraded to "narrow" — the
   evidence gets acknowledged, but a single thin data point doesn't get to
   flip a standing claim.

2. `refine` never fired in the real run (0 of 246 revisions across 4 test
   cases) because its condition — balance > nuance_threshold AND reasoning <
   soundness_threshold — is a narrow intersection that real, multi-source
   evidence rarely lands in (some source almost always has a caveat, so
   balance rarely clears 0.5). Wording-only flaws (vague, conflates_metrics)
   can appear on an otherwise well-supported claim regardless of balance, so
   those flaws now force "refine" even out of the "keep" branch.

Making this a formula rather than asking the challenger "should we flip?" means
the reason a claim reversed on round 3 is visible in the trace as numbers, and
the aggressiveness of evolution is a config knob rather than a prompt rewrite.

Every text change is re-verified against the evidence, so support_score always
describes the CURRENT text and `support_after - support_before` measures whether
revision actually improved grounding (eval/metrics.py reports this as
support_lift).
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

from src.agents.challenger import challenge_claim
from src.agents.reviser import revise_claim
from src.scoring.judge import judge_revision
from src.obs.trace import log_step
from src.orchestrator.config import Config
from src.orchestrator.state import (
    ChallengeRecord,
    Claim,
    ClaimRevision,
    EvidenceChunk,
    ResearchState,
    SubQuestion,
)
from src.scoring.verifier import verify_claims


def _text_fingerprint(text: str) -> str:
    """Whitespace/case-insensitive fingerprint, for detecting repeated wordings."""
    normalized = " ".join((text or "").lower().split())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]


def evidence_pool_for(state: ResearchState, sq: SubQuestion) -> list[EvidenceChunk]:
    """
    All evidence retrieved for a sub-question, across every round so far.

    Chunks carry sub_question_id (set by the researcher). The chunk_id prefix
    fallback keeps this working for states serialized before that field existed.
    """
    pool = [c for c in state.evidence if c.sub_question_id == sq.sq_id]
    if not pool:
        pool = [c for c in state.evidence if c.chunk_id.startswith(f"{sq.sq_id}_")]
    return pool


def select_challenge_evidence(
    pool: list[EvidenceChunk],
    claim: Claim,
    config: Config,
) -> list[EvidenceChunk]:
    """
    Sample a sub-question's evidence pool down to what one challenge prompt can
    usefully carry — maximising SOURCE BREADTH, not chunk count.

    A single arXiv PDF chunks into dozens of pieces, so a real pool runs to
    hundreds of chunks. Two reasons that cannot go into the prompt whole: the
    obvious one (a challenge runs per claim per round, so the prompt is the
    dominant cost of the whole feature), and a subtler one that matters more.

    Evidence balance counts DISTINCT SOURCES. If a naive head-of-list slice
    hands the challenger forty consecutive chunks of one paper, every index it
    returns resolves to the same source, and the balance score degenerates —
    one source lands on both the supporting and refuting side and the claim
    reads as unanimously refuted. Selection therefore round-robins across
    sources so that the indices the challenger picks actually span the
    literature the balance score claims to measure.

    Priority: chunks the claim cites (it must be able to see what it is judging)
    → then breadth across sources, most recent rounds first, because the newest
    evidence is what could change a standing claim.
    """
    if len(pool) <= config.evolution.max_evidence_chunks:
        return pool

    cited_ids = set(claim.evidence_ids)
    cap = config.evolution.max_evidence_chunks
    per_source = max(1, config.evolution.max_chunks_per_source)

    selected: list[EvidenceChunk] = [c for c in pool if c.chunk_id in cited_ids][:cap]
    seen = {c.chunk_id for c in selected}

    # Group the rest by source, newest round first within each source.
    by_source: dict[str, list[EvidenceChunk]] = {}
    for chunk in pool:
        if chunk.chunk_id in seen:
            continue
        by_source.setdefault(chunk.source_title, []).append(chunk)
    for chunks in by_source.values():
        chunks.sort(key=lambda c: -c.retrieval_round)

    # Round-robin across sources so every source is represented before any
    # source gets a second chunk.
    for depth in range(per_source):
        for chunks in by_source.values():
            if len(selected) >= cap:
                break
            if depth < len(chunks):
                selected.append(chunks[depth])
        if len(selected) >= cap:
            break

    return selected[:cap]


def route_operation(challenge, config: Config) -> str:
    """
    Map a challenge onto an operation using the evidence-balance gates.

    Returns one of: keep | refine | narrow | reverse | retract.

    Order matters, and the ordering encodes one rule: CHANGING A CLAIM'S
    POSITION REQUIRES EVIDENCE DOMINANCE, NOT THE CHALLENGER'S SAY-SO. The
    balance gates are checked first and are the only path to reverse/retract. A
    challenger that demands a reversal without the distinct sources to back it
    gets the strongest action the evidence does license — nuance. Otherwise an
    adversarial prompt tuned to be aggressive could flip well-supported claims,
    and "the claim evolved" would mean nothing more than "the critic was loud".
    """
    ev = config.evolution
    balance = challenge.evidence_balance
    reasoning = challenge.reasoning_score
    n_sources = challenge.n_supporting_sources + challenge.n_refuting_sources
    has_reversal_evidence = n_sources >= ev.min_sources_for_reversal

    # STALEMATE: the challenger, having seen this claim's revision history, says
    # its objection would push the claim back toward a wording it already held.
    # Rewriting again would just continue the measured cycle, so the claim
    # stands and gets flagged as contested instead. This is the only branch that
    # depends on the challenger having memory of its own past decisions.
    if challenge.contested_stalemate:
        return "keep"

    # Dominant refuting evidence — the claim's position is no longer the one the
    # literature supports. This is the only gate that can flip a claim, and it
    # requires enough independent sources to be a real signal, not one paper's
    # aside outvoting everything else on the strength of being the only vote.
    if balance < ev.reversal_balance_threshold and has_reversal_evidence:
        if challenge.verdict == "unsupported":
            return "retract"
        return "reverse"

    # Unsupported AND unsound, but the evidence is not lopsided against it:
    # nothing is left to salvage, though this is a reasoning failure rather than
    # a factual reversal.
    if challenge.verdict == "unsupported" and reasoning < ev.reasoning_soundness_threshold:
        return "retract"

    # Real but minority contradicting evidence — this is where nuance enters.
    # A balance that WOULD reverse but lacks source support lands here too.
    if balance <= ev.nuance_balance_threshold:
        return "narrow"

    # The challenger asked for a position change, a qualifier, or called the
    # claim unsupported, on a claim the evidence balance still favours. Nuance
    # is as far as that can go — but it must never fall through to "keep": an
    # adverse verdict always has to change something, or the challenger's
    # objection has silently vanished from the output.
    if challenge.verdict in ("needs_nuance", "needs_reversal", "unsupported"):
        return "narrow"

    # Evidence is lopsidedly in favour, but the inference itself is unsound
    # (overgeneralized, causal claim from correlational evidence, imprecise) —
    # OR the challenger flagged a wording-only flaw even on an otherwise sound,
    # well-supported claim. Either way the position stands; only the phrasing
    # needs fixing.
    wording_flaws = {"vague", "conflates_metrics"}
    if reasoning < ev.reasoning_soundness_threshold or wording_flaws & set(challenge.flaws):
        return "refine"

    return "keep"


def _select_claims_to_challenge(
    state: ResearchState,
    sq: SubQuestion,
    config: Config,
) -> list[Claim]:
    """
    Pick which claims to spend challenge calls on this round.

    Frozen claims (survived `stability_rounds` challenges unchanged) are skipped
    — a claim that keeps surviving the same attack should stop costing money.
    The rest are ordered lowest-confidence-first so that when the per-round cap
    binds, the budget goes to the shakiest claims.
    """
    candidates = [
        c for c in state.claims
        if c.sub_question_id == sq.sq_id and c.is_active and not c.frozen
    ]
    candidates.sort(key=lambda c: (c.confidence if c.confidence is not None else 0.0))
    cap = max(0, config.evolution.max_challenges_per_round)
    return candidates[:cap] if cap else candidates


def evolve_claims(
    state: ResearchState,
    sq: SubQuestion,
    challenger_llm,
    reviser_llm,
    verifier_llm,
    config: Config,
    round_num: int = 0,
    judge_llm=None,
) -> dict:
    """
    Run one evolution pass over a sub-question's active claims.

    Returns a summary dict of what happened this pass (used by the allocator to
    decide whether claims are still churning and by metrics for reporting).
    """
    summary = {
        "challenged": 0, "keep": 0, "refine": 0, "narrow": 0,
        "reverse": 0, "retract": 0, "changed": 0, "stalemate": 0,
    }

    if not config.evolution.enabled:
        return summary

    pool = evidence_pool_for(state, sq)
    if not pool:
        return summary

    targets = _select_claims_to_challenge(state, sq, config)
    if not targets:
        return summary

    def _process(claim: Claim) -> dict:
        """
        Challenge, route, revise and re-verify one claim.

        Returns a counter dict rather than mutating `summary` directly: `+=` on
        shared dict entries is a read-modify-write and would lose updates when
        claims are processed concurrently. Everything else this touches is
        either the claim itself or an atomic list append.
        """
        local = {"challenged": 0, "keep": 0, "refine": 0, "narrow": 0,
                 "reverse": 0, "retract": 0, "changed": 0, "stalemate": 0}
        # Per-claim selection: the cited chunks differ per claim, so the sample
        # must be rebuilt for each one rather than shared across the pass.
        claim_pool = select_challenge_evidence(pool, claim, config)

        challenge = challenge_claim(
            state, claim, claim_pool, challenger_llm, config, round_num=round_num
        )
        local["challenged"] += 1

        operation = route_operation(challenge, config)

        # Record every challenge, including the ones that did not land — the
        # "sound" verdicts are the signal for measuring self-agreement bias.
        record = ChallengeRecord(
            claim_id=claim.claim_id,
            round_num=round_num,
            claim_version=claim.version,
            verdict=challenge.verdict,
            reasoning_score=challenge.reasoning_score,
            evidence_balance=challenge.evidence_balance,
            flaws=challenge.flaws,
            critique=challenge.critique,
            n_supporting_sources=challenge.n_supporting_sources,
            n_refuting_sources=challenge.n_refuting_sources,
            led_to_revision=operation != "keep",
            challenger_model=challenge.challenger_model,
            contested_dimension=challenge.contested_dimension,
            refuting_quotes=challenge.refuting_quotes,
            dropped_ungrounded_refutations=challenge.dropped_ungrounded_refutations,
        )
        state.challenges.append(record)

        # The challenger's diagnosis lands on the claim regardless of whether it
        # triggers a rewrite — reasoning_score and balance feed the confidence
        # scorer, so a claim that survives a challenge is scored MORE
        # confidently than one that was never challenged.
        claim.reasoning_score = challenge.reasoning_score
        claim.evidence_balance = challenge.evidence_balance
        claim.refuting_evidence_ids = challenge.refuting_evidence_ids
        claim.flaws = challenge.flaws

        if operation == "keep":
            local["keep"] += 1
            claim.challenges_survived += 1
            # A declared stalemate is not the same as surviving scrutiny: the
            # challenger still objects, it just recognises that acting on the
            # objection would restart a cycle. Freeze immediately and mark the
            # claim contested so the report says so rather than presenting
            # whichever wording the last pass happened to produce.
            if challenge.contested_stalemate:
                claim.oscillating = True
                claim.frozen = True
                local["stalemate"] += 1
                log_step(
                    state, component="evolution", step="stalemate_declared",
                    input_summary=f"{claim.claim_id} v{claim.version}",
                    output_summary=(
                        "challenger saw the revision history and declined to re-litigate; "
                        "claim held and flagged as genuinely contested"
                    ),
                    latency_ms=0, cost_tokens=0,
                    metadata={"claim_id": claim.claim_id, "critique": challenge.critique[:200]},
                )
            elif claim.challenges_survived >= config.evolution.stability_rounds:
                claim.frozen = True
            return local

        # A claim that changes is no longer stable, whatever its history.
        claim.challenges_survived = 0

        support_before = claim.support_score
        reasoning_before = challenge.reasoning_score
        prev_text = claim.text

        # The reviser must see the SAME sample the challenger judged, or the
        # evidence indices in the critique refer to chunks it cannot see.
        revision = revise_claim(
            state, claim, challenge, operation, claim_pool, reviser_llm, config,
            round_num=round_num,
        )

        if revision.operation == "retract":
            claim.status = "retracted"
            claim.version += 1
            claim.revisions.append(ClaimRevision(
                version=claim.version,
                round_num=round_num,
                operation="retract",
                prev_text=prev_text,
                new_text="",
                rationale=revision.rationale or challenge.critique,
                flaws=challenge.flaws,
                evidence_balance=challenge.evidence_balance,
                support_before=support_before,
                support_after=None,
                reasoning_before=reasoning_before,
                reasoning_after=None,
                challenger_model=challenge.challenger_model,
            ))
            local["retract"] += 1
            local["changed"] += 1
            return local

        if not revision.changed:
            # The reviser was asked to change the claim and returned the same
            # text. Treat it as a survived challenge rather than a silent no-op.
            local["keep"] += 1
            claim.challenges_survived += 1
            return local

        if claim.original_text is None:
            claim.original_text = prev_text

        # Oscillation check: has this claim held this exact text before? If so
        # it is cycling rather than converging, and further challenges are
        # waste — the frozen-pool experiment showed such claims never settle,
        # even against evidence that never changes. Freeze it and record WHY,
        # so the report can say the sources genuinely conflict here instead of
        # silently presenting whichever version the last pass happened to land on.
        if not claim.text_history:
            claim.text_history = [_text_fingerprint(prev_text)]
        new_fp = _text_fingerprint(revision.revised_text)
        if new_fp in claim.text_history:
            claim.oscillating = True
            claim.frozen = True
            log_step(
                state, component="evolution", step="oscillation_detected",
                input_summary=f"{claim.claim_id} v{claim.version}",
                output_summary=(
                    "claim returned to a previous wording — cycling, not converging; "
                    "frozen and flagged as unresolvable with current evidence"
                ),
                latency_ms=0, cost_tokens=0,
                metadata={"claim_id": claim.claim_id, "versions_seen": len(claim.text_history)},
            )
        claim.text_history.append(new_fp)

        claim.text = revision.revised_text
        claim.evidence_ids = revision.evidence_ids or claim.evidence_ids
        claim.version += 1

        # Re-verify: support_score must describe the CURRENT text, otherwise the
        # claim carries a score earned by wording that no longer exists.
        verify_claims(state, [claim], verifier_llm, config)

        # Independent second opinion on quality (see ClaimRevision.judge_verdict
        # docstring): support_after - support_before conflates "more accurate"
        # with "harder to fully entail". The judge answers a structurally
        # different question — blind, order-randomized, which text is a better
        # reflection of the evidence — so it doesn't share that conflation.
        judge_verdict, judge_rationale = None, ""
        if judge_llm is not None:
            judge_verdict, judge_rationale = judge_revision(
                state, prev_text, claim.text, claim_pool, judge_llm, config,
                round_num=round_num, claim_id=claim.claim_id,
            )

        claim.revisions.append(ClaimRevision(
            version=claim.version,
            round_num=round_num,
            operation=revision.operation,
            prev_text=prev_text,
            new_text=claim.text,
            rationale=revision.rationale or challenge.critique,
            flaws=challenge.flaws,
            evidence_balance=challenge.evidence_balance,
            support_before=support_before,
            support_after=claim.support_score,
            reasoning_before=reasoning_before,
            reasoning_after=None,  # re-scored on the next round's challenge
            challenger_model=challenge.challenger_model,
            judge_verdict=judge_verdict,
            judge_rationale=judge_rationale,
        ))

        local[revision.operation] = local.get(revision.operation, 0) + 1
        local["changed"] += 1
        return local

    # The challenge/revise/re-verify cycle is the single most expensive thing
    # the pipeline does -- challenger, judge and reviser together are ~59% of
    # all LLM latency, and each claim is independent of the others.
    ec = getattr(config, "execution", None)
    workers = getattr(ec, "max_claim_workers", 0) if ec else 0
    if getattr(ec, "parallel_claims", False) and len(targets) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool_ex:
            results = list(pool_ex.map(_process, targets))
    else:
        results = [_process(c) for c in targets]

    for r in results:
        if not r:
            continue
        for k, v in r.items():
            summary[k] = summary.get(k, 0) + v

    log_step(
        state,
        component="evolution",
        step="evolve",
        input_summary=f"SQ: {sq.question[:60]}, round {round_num}, {summary['challenged']} challenged",
        output_summary=(
            f"{summary['changed']} revised "
            f"(refine={summary['refine']}, narrow={summary['narrow']}, "
            f"reverse={summary['reverse']}, retract={summary['retract']}), "
            f"{summary['keep']} stood"
        ),
        latency_ms=0,
        cost_tokens=0,
        metadata={"sq_id": sq.sq_id, "round": round_num, **summary},
    )

    return summary


def active_claims_for(state: ResearchState, sq_id: str) -> list[Claim]:
    """Active (non-retracted) claims for a sub-question."""
    return [c for c in state.claims if c.sub_question_id == sq_id and c.is_active]
