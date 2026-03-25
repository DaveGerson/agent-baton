"""``baton context-profile`` -- list and inspect agent context efficiency profiles.

Context profiles measure how efficiently agents use their context window:
how many files they read vs. how many they actually reference in their
output.  High redundancy (many reads, few references) indicates an agent
is doing too much exploration.

Display modes:
    * ``baton context-profile`` -- List recent profiles with summary stats.
    * ``baton context-profile TASK_ID`` -- Detailed profile for a task.
    * ``baton context-profile --generate TASK_ID`` -- Generate a profile
      from trace data and save it.
    * ``baton context-profile --agent NAME`` -- Aggregate stats for an agent.
    * ``baton context-profile --report`` -- Full markdown efficiency report.

Delegates to:
    :class:`~agent_baton.core.observe.context_profiler.ContextProfiler`
"""
from __future__ import annotations

import argparse

from agent_baton.core.observe.context_profiler import ContextProfiler


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "context-profile",
        help="List and inspect agent context efficiency profiles",
    )

    # Positional: optional task_id for detailed profile view.
    p.add_argument(
        "task_id",
        nargs="?",
        metavar="TASK_ID",
        help="Show context profile for a specific task",
    )

    # Flags (mutually exclusive modes).
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--agent",
        metavar="NAME",
        dest="agent_name",
        help="Show aggregate context stats for a specific agent",
    )
    group.add_argument(
        "--generate",
        metavar="TASK_ID",
        dest="generate_task_id",
        help="Generate and save a context profile from trace data",
    )
    group.add_argument(
        "--report",
        action="store_true",
        help="Print a full markdown context efficiency report",
    )

    p.add_argument(
        "--count",
        type=int,
        default=10,
        metavar="N",
        help="Number of recent profiles to list (default: 10)",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    profiler = ContextProfiler()

    # ── baton context-profile --generate TASK_ID ──────────────────────────
    if args.generate_task_id:
        profile = profiler.profile_task(args.generate_task_id)
        if profile is None:
            print(f"No trace found for task '{args.generate_task_id}'.")
            return
        path = profiler.save_profile(profile)
        print(f"Profile generated: {path}")
        _print_profile(profile)
        return

    # ── baton context-profile --agent NAME ────────────────────────────────
    if args.agent_name:
        summary = profiler.agent_summary(args.agent_name)
        print(f"Agent: {args.agent_name}")
        print(f"  Times seen:       {summary['times_seen']}")
        if summary["times_seen"] == 0:
            print("  No data available.")
            return
        print(f"  Avg files read:   {summary['avg_files_read']}")
        print(f"  Avg efficiency:   {summary['avg_efficiency']:.4f}")
        if summary["most_read_files"]:
            print("  Most-read files:")
            for fpath, count in summary["most_read_files"].items():
                print(f"    {count:>3}x  {fpath}")
        if summary["low_efficiency_tasks"]:
            print(f"  Low-efficiency tasks (<0.3): {', '.join(summary['low_efficiency_tasks'])}")
        return

    # ── baton context-profile --report ────────────────────────────────────
    if args.report:
        print(profiler.generate_report())
        return

    # ── baton context-profile TASK_ID ─────────────────────────────────────
    if args.task_id:
        profile = profiler.load_profile(args.task_id)
        if profile is None:
            print(f"No profile found for task '{args.task_id}'.")
            print(f"Tip: run 'baton context-profile --generate {args.task_id}' first.")
            return
        _print_profile(profile)
        return

    # ── baton context-profile [--count N]  (default: list recent) ─────────
    paths = profiler.list_profiles(count=args.count)
    if not paths:
        print("No context profiles found.")
        print("Tip: run 'baton context-profile --generate TASK_ID' to create one.")
        return

    print(f"Recent context profiles ({len(paths)}):")
    for path in paths:
        profile = profiler.load_profile(path.stem)
        if profile is None:
            print(f"  {path.stem}  (unreadable)")
            continue
        agent_count = len(profile.agent_profiles)
        redundancy_pct = f"{profile.redundancy_rate * 100:.1f}%"
        print(
            f"  {profile.task_id:<40}  "
            f"agents={agent_count:<3}  "
            f"reads={profile.total_files_read:<4}  "
            f"redundancy={redundancy_pct}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _print_profile(profile) -> None:
    """Print a human-readable context profile to stdout.

    Displays overall profile metrics (total reads, unique reads,
    redundancy rate) followed by per-agent breakdowns showing
    efficiency scores, file read/write counts, and token estimates.
    Agents with efficiency below 0.3 are flagged as ``[BROAD READER]``.

    Args:
        profile: A :class:`~agent_baton.core.observe.context_profiler.ContextProfile`
            instance.
    """
    print(f"Context Profile: {profile.task_id}")
    print(f"  Created:        {profile.created_at}")
    print(f"  Total reads:    {profile.total_files_read}")
    print(f"  Unique reads:   {profile.unique_files_read}")
    print(f"  Redundant:      {profile.redundant_reads}")
    print(f"  Redundancy:     {profile.redundancy_rate * 100:.1f}%")
    print()

    for ap in profile.agent_profiles:
        flag = "  [BROAD READER]" if ap.efficiency_score < 0.3 else ""
        print(f"  Agent: {ap.agent_name}{flag}")
        print(f"    Efficiency:      {ap.efficiency_score:.4f}")
        print(f"    Files read:      {len(ap.files_read)}")
        print(f"    Files written:   {len(ap.files_written)}")
        print(f"    Files referenced:{len(ap.files_referenced)}")
        print(f"    Context tokens:  ~{ap.context_tokens_estimate}")
        print(f"    Output tokens:   ~{ap.output_tokens_estimate}")
        if ap.files_read:
            print("    Read:")
            for f in ap.files_read:
                print(f"      {f}")
        if ap.files_written:
            print("    Written:")
            for f in ap.files_written:
                print(f"      {f}")
        print()
