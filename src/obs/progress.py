"""
Live process narration — the agent saying what it is doing, while it does it.

WHY THIS IS SEPARATE FROM THE TRACE. `src/obs/trace.py` already records every
step to `trace.jsonl`, and that is the right substrate for *post-hoc* debugging:
structured, complete, machine-readable, replayable. But it answers "what
happened three steps ago" only *after* the run is over, and its summaries are
written for a machine (`verdict=needs_nuance, reasoning=0.60, balance=+0.20`).

This module answers a different question, for a different audience, at a
different time: what is the system doing *right now*, and *why*. A user watching
a five-minute research run should be able to see it decompose the question,
decide that sub-question 3 looks hard and deserves more compute, search, find
thin evidence, go back with a different query, challenge its own claim, and
retract it — as it happens, in prose. That is the difference between a system
you trust and a system that goes quiet for five minutes and hands you a
document.

DESIGN. Narration is a *view over the existing trace*, not a parallel logging
system. `log_step` forwards every entry here, and per-component renderers turn
them into human-readable lines. That means narration cannot drift out of sync
with the trace — anything that logs is automatically narratable — and adding a
new agent does not require also remembering to narrate it.

Where the trace's machine summary genuinely cannot express the *reasoning*
behind a decision ("spending 3 rounds on this because it looks contested"),
agents call `narrate_decision()` explicitly. Those are deliberately rare: they
mark the handful of points where the system makes a judgment call the user
would otherwise have to infer.

Narration writes to stderr so it never contaminates piped stdout (report text,
JSON), and is a no-op unless enabled — the default for library use is silence.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field


_print_lock = threading.Lock()


# ANSI styling, disabled automatically when not attached to a terminal.
class _Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def cyan(self, t): return self._wrap("36", t)
    def green(self, t): return self._wrap("32", t)
    def yellow(self, t): return self._wrap("33", t)
    def red(self, t): return self._wrap("31", t)
    def magenta(self, t): return self._wrap("35", t)


@dataclass
class ProgressReporter:
    """
    Renders pipeline activity as live prose. One instance per process; see the
    module-level `get_reporter()` / `enable()`.
    """
    enabled: bool = False
    verbose: bool = False          # include per-claim detail (very chatty)
    stream: object = field(default_factory=lambda: sys.stderr)
    _phase: str = ""

    def __post_init__(self):
        self.style = _Style(enabled=getattr(self.stream, "isatty", lambda: False)())
        self._local = threading.local()

    # Indentation is per-thread. Parallel workers each track their own nesting
    # depth; a single shared counter would interleave into meaningless output.
    @property
    def _indent(self) -> int:
        return getattr(self._local, "indent", 0)

    @_indent.setter
    def _indent(self, v: int) -> None:
        self._local.indent = v

    # --- primitives -------------------------------------------------------

    def _emit(self, text: str, indent: int | None = None) -> None:
        if not self.enabled:
            return
        pad = "  " * (self._indent if indent is None else indent)
        line = f"{pad}{text}"
        # When sub-questions run in parallel, interleaved narration is unreadable
        # -- three workers emitting alternating lines produces a transcript no
        # human can follow. So a worker buffers its own lines and flushes them
        # as one contiguous block when its sub-question finishes. Serial mode
        # has no buffer set and prints straight through.
        buf = getattr(self._local, "buffer", None)
        if buf is not None:
            buf.append(line)
            return
        with _print_lock:
            print(line, file=self.stream, flush=True)

    def begin_buffered(self) -> None:
        """Start capturing this thread's narration instead of printing it."""
        self._local.buffer = []

    def flush_buffered(self) -> None:
        """Print everything this thread captured, as one uninterrupted block."""
        buf = getattr(self._local, "buffer", None)
        self._local.buffer = None
        if not buf or not self.enabled:
            return
        with _print_lock:
            for line in buf:
                print(line, file=self.stream, flush=True)

    def phase(self, name: str, detail: str = "") -> None:
        """Top-level pipeline phase banner."""
        if not self.enabled:
            return
        self._phase = name
        self._indent = 0
        s = self.style
        self._emit("")
        self._emit(s.bold(s.cyan(f"▸ {name}")) + (s.dim(f"  {detail}") if detail else ""))
        self._indent = 1

    def sub(self, text: str) -> None:
        self._emit(self.style.dim("· ") + text)

    def detail(self, text: str) -> None:
        """Only shown in verbose mode — per-claim noise."""
        if self.verbose:
            self._emit(self.style.dim(f"    {text}"))

    def decision(self, what: str, because: str) -> None:
        """
        A judgment call the user would otherwise have to infer. Deliberately
        formatted as "X — because Y" so the reason is never optional.
        """
        s = self.style
        self._emit(f"{s.yellow('◆')} {what} {s.dim('— ' + because)}")

    def warn(self, text: str) -> None:
        self._emit(f"{self.style.red('!')} {text}")

    def push(self, text: str) -> None:
        self._emit(self.style.bold(text))
        self._indent += 1

    def pop(self) -> None:
        self._indent = max(1, self._indent - 1)

    # --- trace-driven narration -------------------------------------------

    def on_log_step(self, component: str, step: str, input_summary: str,
                    output_summary: str, metadata: dict) -> None:
        """
        Render a trace entry as prose. Called by trace.log_step for every step,
        so narration stays automatically in sync with what is actually recorded.
        """
        if not self.enabled:
            return
        handler = getattr(self, f"_on_{component}", None)
        if handler:
            handler(step, input_summary, output_summary, metadata)

    # Per-component renderers. Each translates the machine summary into the
    # thing a human actually wants to know.

    def _on_planner(self, step, inp, out, meta):
        self.sub(f"Decomposed the question into {out}")

    def _on_difficulty(self, step, inp, out, meta):
        if step == "estimate":
            d = meta.get("difficulty", 0)
            label = "looks hard" if d > 0.5 else "looks straightforward"
            self.detail(f"difficulty {d:.2f} ({label})")
        elif step == "update":
            self.detail(f"difficulty updated: {out}")

    def _on_allocator(self, step, inp, out, meta):
        if step == "allocate":
            budget = meta.get("budget")
            diff = meta.get("difficulty")
            if budget is not None:
                self.decision(
                    f"Allocating {budget} retrieval round(s)",
                    f"estimated difficulty {diff:.2f}" if diff is not None
                    else "difficulty estimate",
                )
        elif step == "extend_budget":
            self.decision(f"Extending the budget", out)
        elif step == "continue_on_churn":
            self.decision("Searching again", "claims are still being revised — not converged yet")

    def _on_query_reformulator(self, step, inp, out, meta):
        if step == "reformulate":
            gap = meta.get("gap", "")
            q = meta.get("query", "")
            if gap:
                self.sub(f"Previous search left a gap: {self.style.dim(gap[:110])}")
            self.decision(f"Searching again with a different query",
                          f'"{q[:90]}"')
        elif step == "fallback":
            self.detail("reformulation unusable — reusing the original question")

    def _on_researcher(self, step, inp, out, meta):
        q = meta.get("query", "")
        n = meta.get("new_chunks", 0)
        rnd = meta.get("round", "?")
        self.sub(f"Round {rnd}: searched arXiv + web for "
                 f"{self.style.dim(chr(34) + q[:80] + chr(34))} → {n} evidence chunks")
        if n == 0:
            self.warn("no evidence returned for this query")

    def _on_extractor(self, step, inp, out, meta):
        self.sub(f"Extracted {meta.get('num_claims', '?')} candidate claims from that evidence")

    def _on_verifier(self, step, inp, out, meta):
        if step == "detect_contradictions":
            self.sub(f"Cross-source contradiction check: {out}")

    def _on_challenger(self, step, inp, out, meta):
        verdict = meta.get("verdict")
        if verdict and verdict != "sound":
            self.detail(f"challenged a claim → {verdict} "
                        f"(reasoning {meta.get('reasoning_score', 0):.2f}, "
                        f"balance {meta.get('evidence_balance', 0):+.2f})")
        dropped = meta.get("dropped_ungrounded_refutations", 0)
        if dropped:
            self.detail(f"dropped {dropped} refutation(s) that misquoted the evidence")

    def _on_reviser(self, step, inp, out, meta):
        if meta.get("changed"):
            self.detail(f"{step}: {inp[:70]} → {out[:70]}")

    def _on_evolution(self, step, inp, out, meta):
        changed = meta.get("changed", 0)
        challenged = meta.get("challenged", 0)
        if not challenged:
            return
        bits = []
        for op in ("narrow", "reverse", "retract", "refine"):
            if meta.get(op):
                bits.append(f"{meta[op]} {op}")
        detail = ", ".join(bits) if bits else "no changes needed"
        self.sub(f"Re-examined {challenged} existing claim(s) against all evidence "
                 f"so far → {detail}")
        if meta.get("reverse") or meta.get("retract"):
            self.decision(
                f"Changed position on {meta.get('reverse', 0) + meta.get('retract', 0)} claim(s)",
                "newer evidence outweighed what they were originally based on",
            )

    def _on_synthesizer(self, step, inp, out, meta):
        self.sub(f"Writing the report from {inp}")

    def _on_report_loop(self, step, inp, out, meta):
        if step == "mechanical_checks":
            self.sub(f"Automated checks on the draft: {out}")
        elif step == "reopen_research":
            self.decision("Going back for more evidence", out)
        elif step == "revise":
            self.sub(f"Rewrote the report: {out}")
        elif step == "accept":
            self._emit(self.style.green("✓ ") + "Report accepted — it answers the question")
        elif step == "stop_not_improving":
            self.warn(f"Stopping revision: {out}")

    def _on_report_critic(self, step, inp, out, meta):
        verdict = meta.get("verdict")
        n = meta.get("n_defects", 0)
        high = meta.get("n_high", 0)
        if verdict == "accept":
            return  # report_loop narrates the acceptance
        self.sub(f"Reviewed the draft → {verdict}: {n} issue(s), {high} serious")
        for dt in dict.fromkeys(meta.get("defect_types", [])):
            self.detail(f"issue: {dt}")

    def _on_judge(self, step, inp, out, meta):
        pass  # too granular for live narration; stays in the trace

    def _on_pipeline(self, step, inp, out, meta):
        if step == "end":
            s = self.style
            self._indent = 0
            self._emit("")
            self._emit(s.bold(s.green("▸ Done")) + s.dim(f"  {out}"))


# --- module-level singleton -------------------------------------------------

_reporter = ProgressReporter(enabled=False)


def get_reporter() -> ProgressReporter:
    return _reporter


def enable(verbose: bool = False, stream=None) -> ProgressReporter:
    """Turn on live narration for this process."""
    global _reporter
    _reporter = ProgressReporter(
        enabled=True, verbose=verbose, stream=stream or sys.stderr
    )
    return _reporter


def disable() -> None:
    global _reporter
    _reporter = ProgressReporter(enabled=False)


def narrate_phase(name: str, detail: str = "") -> None:
    _reporter.phase(name, detail)


def narrate(text: str) -> None:
    _reporter.sub(text)


def narrate_decision(what: str, because: str) -> None:
    """
    Surface a judgment call and its reason. Use sparingly — only where the trace
    records *what* happened but not *why* it was chosen.
    """
    _reporter.decision(what, because)


def push(text: str) -> None:
    _reporter.push(text)


def pop() -> None:
    _reporter.pop()
