"""
Structured JSONL tracing — every component logs inputs/outputs/latency/cost.

The trace is the substrate for two different consumers:
  - post-hoc debugging and replay, via trace.jsonl / state.json
  - LIVE narration, via src/obs/progress.py

Both are driven from this one `log_step` call, deliberately. Narration is a view
over the trace rather than a parallel logging path, so the two cannot drift out
of sync and a newly added agent becomes narratable for free.
"""

import json
import time
from pathlib import Path
from typing import Any

from src.obs.progress import get_reporter
from src.orchestrator.state import ResearchState, TraceEntry


def log_step(
    state: ResearchState,
    component: str,
    step: str,
    input_summary: str,
    output_summary: str,
    latency_ms: float = 0.0,
    cost_tokens: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a trace entry to state."""
    entry = TraceEntry(
        component=component,
        step=step,
        input_summary=input_summary,
        output_summary=output_summary,
        latency_ms=latency_ms,
        cost_tokens=cost_tokens,
        metadata=metadata or {},
    )
    state.trace.append(entry)
    state.total_tokens += cost_tokens
    state.total_latency_ms += latency_ms

    # Live narration is a view over the trace — no-op unless enabled.
    get_reporter().on_log_step(
        component, step, input_summary, output_summary, metadata or {}
    )


def save_trace(state: ResearchState, output_path: str | Path) -> None:
    """Write the full trace as JSONL (one JSON object per line)."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for entry in state.trace:
            f.write(entry.model_dump_json() + "\n")


def save_full_state(state: ResearchState, output_path: str | Path) -> None:
    """Write the full state as a single JSON file for replay/debugging."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state.model_dump(), f, indent=2, default=str)


class Timer:
    """Context manager for measuring latency."""
    def __init__(self):
        self.ms = 0.0

    def __enter__(self):
        self._start = time.time()
        return self

    def __exit__(self, *args):
        self.ms = (time.time() - self._start) * 1000
