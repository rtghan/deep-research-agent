"""
Global round scheduler — an ALTERNATE strategy for spending retrieval effort.

WHY THIS EXISTS (the failure it replaces)
-----------------------------------------
The original allocator asks, per sub-question and in isolation:

    budget = int(min_budget + difficulty * (max_budget - min_budget))
    while rounds_used < budget: ...

The 7-test-case evaluation (TESTING.md section 11, DECISIONS.md D023) showed
this can effectively never grant a third round. With min=1, max=4, a budget of 3
requires difficulty >= 0.667. Difficulty updates as
`0.6*(1 - avg_confidence) + 0.4*linguistic`, so clearing 0.667 at a typical
linguistic difficulty of 0.3 requires average claim confidence <= 0.09 — i.e.
"everything we found is worthless." Observed median claim confidence was 0.85,
observed difficulty spanned 0.13-0.31, and **0 of 35 sub-questions ever crossed
the threshold**. Multi-round research was unreachable in practice, so
`stability_rounds` and the whole convergence story went untested.

THE DIAGNOSIS is that this is a THRESHOLD CALIBRATION failure, not a signal
failure. Difficulty discriminated fine — 0.13 vs 0.31 is a real 2.4x spread — it
just never cleared an arbitrary absolute bar. Rescaling the bar would only move
the problem.

THE FIX is to stop using absolute thresholds. Ask "which sub-question most
deserves the next round?" instead of "does this sub-question deserve more
rounds?". An argmax has nothing to calibrate: even when every difficulty sits in
[0.13, 0.31], ranking still allocates differentially. The saturation problem
disappears by construction, without changing the difficulty formula at all.

WHAT CHANGES
------------
- A single global `round_pool` replaces per-sub-question budgets. Total cost
  becomes a direct knob rather than an emergent consequence of per-item
  thresholds (the 12-hour and 2.3-hour eval runs happened because nothing was
  holding a total).
- Allocation is driven by OBSERVED YIELD, not only predicted difficulty: a
  sub-question whose last round actually changed standing claims earns another
  pull; one that yielded nothing is deprioritized. The old design never learned
  from its own allocation history.
- `pool == n_sub_questions` degrades exactly to the uniform baseline, so the
  ablation baseline becomes a parameter rather than a separate code path.

WHAT THIS DOES NOT FIX
----------------------
This changes WHERE effort goes, not WHETHER the inner loop converges. If claim
evolution never settles on a fixed evidence pool, a scheduler just distributes
non-convergence more evenly. That question is measured separately by the
frozen-pool experiment (see TESTING.md).

This is deliberately additive: `adaptive.strategy: "threshold" | "scheduler"`
selects between them so both remain runnable and directly comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.obs.progress import get_reporter
from src.obs.trace import log_step
from src.orchestrator.config import Config
from src.orchestrator.evolution import active_claims_for
from src.orchestrator.state import ResearchState, SubQuestion


@dataclass
class RoundOutcome:
    """What one round actually bought, used to score the next allocation."""
    new_evidence: int = 0
    new_claims: int = 0
    claims_changed: int = 0     # revisions to PRE-EXISTING claims
    found_nothing: bool = False


def _confidence_stats(state: ResearchState, sq: SubQuestion) -> tuple[float, float, int]:
    """Returns (mean confidence, spread, n) over the sub-question's active claims."""
    claims = active_claims_for(state, sq.sq_id)
    if not claims:
        return 0.0, 0.0, 0
    vals = [c.confidence or 0.0 for c in claims]
    mean = sum(vals) / len(vals)
    spread = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
    return mean, spread, len(vals)


def marginal_value(
    state: ResearchState,
    sq: SubQuestion,
    outcome: RoundOutcome | None,
    config: Config,
) -> float:
    """
    Expected value of giving THIS sub-question the next round.

    Deliberately a product of three terms rather than a weighted sum: if any one
    of them is ~0 (nothing uncertain, last round yielded nothing, coverage
    already good) the sub-question should not win the next pull, and a sum would
    let a single large term paper over that.
    """
    sch = config.adaptive
    mean_conf, spread, n_claims = _confidence_stats(state, sq)

    # 1. UNCERTAINTY — spread matters as much as the mean. Claims at 0.9 and 0.3
    #    are unresolved in a way a uniform 0.6 is not; a mean-only signal (what
    #    the old allocator used) cannot see that difference at all.
    uncertainty = (1.0 - mean_conf) + 0.5 * spread

    # 2. OBSERVED YIELD — did the LAST round on this sub-question actually move
    #    anything? This is the term the old design lacked entirely. Never let it
    #    reach zero on the first pass, or a sub-question can never be retried.
    if outcome is None:
        yield_term = 1.0                      # untried: optimistic, explore it
    elif outcome.found_nothing:
        yield_term = 0.05                     # retrieval is dry; stop paying
    else:
        moved = outcome.claims_changed + outcome.new_claims
        yield_term = min(1.0, 0.15 + moved / 8.0)

    # 3. OSCILLATION PENALTY — this term exists because the frozen-pool
    #    experiment (TESTING.md section 14) broke the naive version of the
    #    yield term above. Claims that cycle between wordings change EVERY
    #    round, forever, even against evidence that never changes: 6/12 claims
    #    did exactly that, at a flat ~50% keep rate over 5 passes. A yield
    #    signal that counts "claims changed" therefore reads perpetual thrash
    #    as perpetual productivity, and would pour the entire pool into the one
    #    sub-question least able to use it.
    #
    #    Oscillation is treated as evidence the sub-question is CONTESTED
    #    rather than under-researched. More retrieval does not resolve a
    #    genuine conflict in the literature, so this suppresses spending
    #    instead of attracting it.
    claims = active_claims_for(state, sq.sq_id)
    if claims:
        osc_frac = sum(1 for c in claims if c.oscillating) / len(claims)
        yield_term *= max(0.1, 1.0 - osc_frac)

    # 4. COVERAGE DEFICIT — thin claim sets are worth more evidence than
    #    already well-covered ones.
    target = max(1, sch.target_claims_per_sub_question)
    coverage_deficit = max(0.1, min(1.0, 1.0 - (n_claims / target)))

    return uncertainty * yield_term * coverage_deficit


def run_scheduled_research(
    state: ResearchState,
    config: Config,
    run_round,
) -> None:
    """
    Allocate retrieval rounds from a single global pool, always to the
    highest-marginal-value sub-question.

    `run_round(sq, round_num) -> RoundOutcome` performs one full round
    (retrieve → extract → verify → evolve → score). The scheduler owns *which*
    sub-question and *how many* rounds; the round body is shared verbatim with
    the threshold strategy so the two differ only in scheduling.
    """
    reporter = get_reporter()
    sch = config.adaptive
    sqs = state.plan.sub_questions if state.plan else []
    if not sqs:
        return

    last: dict[str, RoundOutcome] = {}

    # COLD START: every sub-question gets one round. There is no way to estimate
    # marginal value before seeing what a sub-question returns, and leaving one
    # unexamined is worse than any misallocation. This is also the only honest
    # remaining job of the pre-retrieval linguistic difficulty estimate: it
    # orders this sweep, so the most promising is examined first.
    for sq in sorted(sqs, key=lambda s: -s.difficulty):
        reporter.push(f"[{sq.sq_id}] {sq.question}")
        last[sq.sq_id] = run_round(sq, sq.rounds_used + 1)
        reporter.pop()

    pool = max(0, sch.total_round_pool - len(sqs))
    log_step(
        state, "scheduler", "cold_start",
        f"{len(sqs)} sub-questions",
        f"one round each; {pool} rounds left in the global pool",
        metadata={"pool_remaining": pool, "total_pool": sch.total_round_pool},
    )

    # ALLOCATION: spend the remaining pool one round at a time, always on the
    # current argmax. Re-ranking after every round is the point — the scheduler
    # learns from what each round returned.
    while pool > 0:
        scored = [
            (marginal_value(state, sq, last.get(sq.sq_id), config), sq)
            for sq in sqs
            if sq.rounds_used < sch.max_rounds_per_sub_question
        ]
        if not scored:
            break

        scored.sort(key=lambda t: -t[0])
        best_value, best_sq = scored[0]

        if best_value < sch.marginal_value_floor:
            log_step(
                state, "scheduler", "stop_below_floor",
                f"best={best_sq.sq_id} value={best_value:.3f}",
                f"no sub-question clears the floor ({sch.marginal_value_floor}); "
                f"{pool} rounds left unspent",
                metadata={"best_value": best_value, "pool_unspent": pool},
            )
            reporter.decision(
                f"Stopping early with {pool} round(s) unspent",
                "no sub-question would gain enough from another round",
            )
            break

        reporter.push(f"[{best_sq.sq_id}] {best_sq.question}")
        reporter.decision(
            f"Spending the next round here (round {best_sq.rounds_used + 1})",
            f"highest marginal value {best_value:.2f} of {len(scored)} candidates",
        )
        log_step(
            state, "scheduler", "allocate",
            f"pool={pool}",
            f"{best_sq.sq_id} (value {best_value:.3f})",
            metadata={
                "sq_id": best_sq.sq_id,
                "value": best_value,
                "pool_remaining": pool - 1,
                "ranking": [(s.sq_id, round(v, 3)) for v, s in scored[:4]],
            },
        )

        last[best_sq.sq_id] = run_round(best_sq, best_sq.rounds_used + 1)
        reporter.pop()
        pool -= 1

    log_step(
        state, "scheduler", "done",
        f"pool exhausted or floor reached",
        f"rounds per sub-question: {[(s.sq_id, s.rounds_used) for s in sqs]}",
        metadata={"rounds_used": {s.sq_id: s.rounds_used for s in sqs}},
    )
