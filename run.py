#!/usr/bin/env python3
"""
Deep Research Agent — CLI entry point.

Usage:
    python run.py "your research query"
    python run.py --demo                        # runs a demo query
    python run.py --openrouter --demo            # demo with OpenRouter
    python run.py --openrouter --eval            # eval with OpenRouter
    python run.py --openrouter --ablation        # full ablation with OpenRouter
    python run.py --mock --demo                  # demo with mock LLM (no key)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _load_env_file():
    """Load .env file if present (no external dependency)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        import os
        os.environ.setdefault(key, value)


_load_env_file()


def _load_config(args):
    from src.orchestrator.config import Config
    if args.openrouter:
        cfg = Config.load("configs/openrouter.yaml")
    elif args.config:
        cfg = Config.load(args.config)
    else:
        cfg = Config.load()

    # --serial wins over --parallel, so it is always a safe way to get the
    # reference behaviour back regardless of what the config file says.
    if getattr(args, "serial", False):
        cfg.execution.parallel_sub_questions = False
        cfg.execution.parallel_test_cases = False
    elif getattr(args, "parallel", False):
        cfg.execution.parallel_sub_questions = True
        cfg.execution.parallel_test_cases = True
    if getattr(args, "workers", None):
        cfg.execution.max_workers = args.workers
        cfg.execution.max_case_workers = args.workers

    if getattr(args, "fast", False):
        # Everything that reduces wall-clock for a single real question, at the
        # cost of money and breadth. Concurrency at both levels is the main
        # lever: claim-level covers ~83% of LLM latency, sub-question level the
        # rest. Fast models matter second -- the slow challenger dominated.
        cfg.execution.parallel_sub_questions = True
        cfg.execution.max_workers = 5
        cfg.execution.parallel_claims = True
        cfg.execution.max_claim_workers = 10
        cfg.retrieval.fetch_fulltext = False   # PDF downloads are invisible in the trace and huge
        cfg.retrieval.search_results_per_query = 3
        cfg.evolution.judge_revisions = False  # 15% of LLM time, invisible on stage
        cfg.report_correction.max_passes = 1
        cfg.adaptive.enabled = False
        cfg.adaptive.max_budget = 2

    if getattr(args, "demo_live", False):
        # Tuned so a real run finishes inside a demo slot while still showing
        # every behaviour worth showing: reformulation on round 2, a full
        # evolution pass, and one report-critique cycle. Everything here trades
        # breadth for wall-clock; it is not the configuration to evaluate with.
        cfg.execution.parallel_sub_questions = True
        cfg.execution.max_workers = 4
        cfg.adaptive.enabled = False        # fixed budget, no early stopping
        cfg.adaptive.max_budget = 2         # round 2 exists, so reformulation fires
        cfg.retrieval.search_results_per_query = 2
        cfg.execution.max_sub_questions = 2   # biggest lever on wall-clock
        cfg.retrieval.fetch_fulltext = False  # PDF downloads dominate wall-clock
        cfg.evolution.max_challenges_per_round = 2
        cfg.evolution.max_evidence_chunks = 6
        cfg.evolution.judge_revisions = False  # ~2.5s per revision, invisible on stage
        cfg.report_correction.max_passes = 1
        cfg.llm.max_tokens = 900
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Deep Research Agent")
    parser.add_argument("query", nargs="?", help="Research query to investigate")
    parser.add_argument("--demo", action="store_true", help="Run a demo query")
    parser.add_argument("--eval", action="store_true", help="Run evaluation harness")
    parser.add_argument("--ablation", action="store_true", help="Run ablation study")
    parser.add_argument("--config", default=None, help="Path to config YAML")
    parser.add_argument("--output", default="outputs", help="Output directory")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM (no API key needed)")
    parser.add_argument("--openrouter", action="store_true", help="Use OpenRouter config")
    parser.add_argument("--narrate", "-n", action="store_true",
                        help="Narrate the research process live (what it's investigating and why)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Narrate with per-claim detail (implies --narrate)")
    parser.add_argument("--parallel", action="store_true",
                        help="Run sub-questions concurrently (and test cases, under --eval)")
    parser.add_argument("--serial", action="store_true",
                        help="Force serial execution, overriding the config")
    parser.add_argument("--workers", type=int, default=None,
                        help="Worker count for --parallel (default 4)")
    parser.add_argument("--replay", metavar="TRACE",
                        help="Replay a stored trace.jsonl through the live narrator (offline)")
    parser.add_argument("--speed", type=float, default=6.0,
                        help="Replay speed multiplier (default 6)")
    parser.add_argument("--pause-at", default=None, metavar="COMPONENT[/STEP]",
                        help="Pause replay at a step, e.g. evolution/evolve")
    parser.add_argument("--fast", action="store_true",
                        help="Maximum concurrency + fast models for one real question")
    parser.add_argument("--demo-live", action="store_true",
                        help="Live run tuned to finish inside a demo slot")
    args = parser.parse_args()

    if args.replay:
        from src.obs.progress import enable
        from src.obs.replay import replay_trace
        enable(verbose=args.verbose)
        replay_trace(args.replay, speed=args.speed, pause_at=args.pause_at)
        return

    # Live narration writes to stderr, so `run.py -n "query" > report.md` still
    # produces a clean report on stdout.
    if args.narrate or args.verbose:
        from src.obs.progress import enable
        enable(verbose=args.verbose)

    if args.ablation:
        from ablations.run_ablation import run_ablation
        config = _load_config(args)
        # Pass openrouter config through environment
        if args.openrouter:
            import os
            os.environ.setdefault("OPENROUTER_API_KEY", "")
        run_ablation(use_mock=args.mock, config=config)
        return

    if args.eval:
        from eval.harness import run_eval
        config = _load_config(args)
        run_eval(config, use_mock=args.mock)
        return

    # Single query mode
    if args.demo:
        query = "What are the key architectural innovations in the Transformer paper and how do they compare to earlier RNN-based approaches?"
    elif args.query:
        query = args.query
    else:
        parser.print_help()
        return

    from src.orchestrator.pipeline import run_research
    config = _load_config(args)

    print(f"\n{'='*60}")
    print(f"Research Query: {query}")
    print(f"{'='*60}\n")

    state = run_research(query, config, output_dir=args.output, use_mock=args.mock)

    print(f"\n{'='*60}")
    print(f"Research Complete!")
    print(f"{'='*60}")
    print(f"  Claims: {len(state.claims)}")
    print(f"  Evidence chunks: {len(state.evidence)}")
    print(f"  Contradictions: {len(state.contradictions)}")
    print(f"  Total tokens: {state.total_tokens}")
    print(f"  Total latency: {state.total_latency_ms:.0f}ms")
    print(f"\nReport saved to: {args.output}/report.md")
    print(f"Trace saved to: {args.output}/trace.jsonl")
    print(f"Full state: {args.output}/state.json")
    print(f"\n{'='*60}")
    print("REPORT PREVIEW:")
    print(f"{'='*60}\n")
    print(state.report[:3000] if state.report else "(no report)")
    if len(state.report or "") > 3000:
        print("\n... (truncated, see full report in outputs/report.md)")


if __name__ == "__main__":
    main()
