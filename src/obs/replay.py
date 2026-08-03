"""
Replay a completed run's narration from its stored trace.

This exists for two reasons, and the second one matters more than the first.

The practical reason: a real run takes minutes and depends on arXiv, a web
search backend and two model providers all behaving. That is a bad bet for a
five-minute slot in front of an audience, and an unrecoverable one if it fails
halfway. Replay is deterministic, offline, free, and can be paused or rewound
when somebody interrupts to ask why a particular claim was reversed.

The substantive reason: narration is already a view over the trace rather than
a parallel logging path, so a replay is not a re-enactment — it is the same
renderer fed the same events. If `trace.jsonl` were missing anything the
narration needs, the replay would visibly come out incomplete. Reconstructing
the entire run from nothing but the trace is therefore a demonstration that the
trace really is a complete record of how each claim was produced, which is the
property the whole system is built around.

Phase banners and sub-question headers are not themselves trace entries — they
are emitted by the pipeline around the work. They get reconstructed here from
component transitions and the `sq_id` in entry metadata, which is why replay
output matches a live run closely but not byte-for-byte.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from src.obs.progress import get_reporter


# component -> the phase banner that precedes it in a live run
_PHASE_FOR = {
    "planner": "Understanding the question",
    "researcher": "Researching each sub-question",
    "verifier": None,          # inside the research phase, no new banner
    "synthesizer": "Writing the report",
    "report_critic": "Reviewing my own report",
    "report_loop": "Reviewing my own report",
}


def _sq_of(entry: dict) -> tuple[str, str] | None:
    """
    Return (identity, display_text) for the sub-question an entry belongs to.

    Entries encode this inconsistently — some carry `sq_id` in metadata, others
    only put the question text in `input_summary` with trailing detail appended
    ("SQ: ..., difficulty=0.38" vs "SQ: ..., 35 chunks"). Keying on the raw
    string would treat those as different sub-questions and re-print the header
    on every step, so the identity is normalised to the question prefix.
    """
    meta = entry.get("metadata") or {}
    inp = entry.get("input_summary", "")

    text = ""
    if inp.startswith("SQ: "):
        text = inp[4:]
        # strip the trailing ", difficulty=0.38" / ", 35 chunks" style suffix
        text = re.sub(r",\s*(difficulty=[\d.]+|\d+ chunks|round \d+.*)$", "", text).strip()
    elif ":" in inp and inp.split(":")[0].startswith("sq_"):
        text = inp.split(":", 1)[1].strip()

    sq_id = meta.get("sq_id") or meta.get("sub_question_id")
    if sq_id and not text:
        return (sq_id, sq_id)
    if text:
        # normalise on a prefix so differing suffixes collapse to one identity
        return (text[:60].lower(), text)
    return None


def replay_trace(
    path: str | Path,
    speed: float = 1.0,
    pause_at: str | None = None,
    max_delay: float = 0.35,
) -> None:
    """
    Re-emit a stored trace through the live narrator.

    `speed` multiplies playback rate; delays are derived from each step's
    recorded `latency_ms` so the rhythm reflects what was actually slow, then
    clamped by `max_delay` so a 40-second PDF fetch doesn't stall the room.

    `pause_at` is "component" or "component/step" — playback stops there and
    waits for Enter, which is how you hold on the interesting moment instead of
    narrating over it.
    """
    entries = [json.loads(line) for line in open(path) if line.strip()]
    if not entries:
        print(f"(empty trace: {path})")
        return

    reporter = get_reporter()
    if not reporter.enabled:
        from src.obs.progress import enable
        reporter = enable(verbose=False)

    want_c, _, want_s = (pause_at or "").partition("/")
    seen_phase: str | None = None
    seen_sq: str | None = None
    total_tokens = 0

    for entry in entries:
        component = entry.get("component", "")
        step = entry.get("step", "")
        total_tokens += entry.get("cost_tokens", 0) or 0

        # Reconstruct the phase banner the pipeline would have printed.
        phase = _PHASE_FOR.get(component)
        if phase and phase != seen_phase:
            if seen_sq is not None:
                reporter.pop()
                seen_sq = None
            reporter.phase(phase)
            seen_phase = phase

        # Reconstruct the per-sub-question header, once per sub-question.
        found = _sq_of(entry)
        if found and component in ("researcher", "extractor", "evolution",
                                   "query_reformulator", "allocator", "difficulty"):
            identity, display = found
            if identity != seen_sq:
                if seen_sq is not None:
                    reporter.pop()
                reporter.push(display)
                seen_sq = identity

        reporter.on_log_step(
            component, step,
            entry.get("input_summary", ""),
            entry.get("output_summary", ""),
            entry.get("metadata") or {},
        )

        if want_c and component == want_c and (not want_s or step == want_s):
            try:
                input("\n    ⏸  paused — press Enter to continue ")
            except EOFError:
                pass

        delay = min(max_delay, (entry.get("latency_ms", 0) or 0) / 1000.0)
        if speed > 0 and delay > 0:
            time.sleep(delay / speed)

    if seen_sq is not None:
        reporter.pop()
    print(f"\n  replayed {len(entries)} steps · {total_tokens:,} tokens · from {path}")
