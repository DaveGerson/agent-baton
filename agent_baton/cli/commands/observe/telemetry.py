"""``baton telemetry`` -- show or clear agent telemetry events.

Telemetry events track fine-grained agent activity: file reads, file
writes, tool invocations, and other observable actions.  This command
provides summary, per-agent, and recent-event views plus a clear
operation.

Display modes:
    * ``baton telemetry`` -- Summary with event counts by agent and type.
    * ``baton telemetry --agent NAME`` -- Events for a specific agent.
    * ``baton telemetry --recent N`` -- Last N telemetry events.
    * ``baton telemetry --clear`` -- Clear the telemetry log.

Delegates to:
    :class:`~agent_baton.core.observe.telemetry.AgentTelemetry`
"""
from __future__ import annotations

import argparse

from agent_baton.core.observe.telemetry import AgentTelemetry


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("telemetry", help="Show or clear agent telemetry events")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--agent", metavar="NAME", help="Show events for a specific agent",
    )
    group.add_argument(
        "--recent", type=int, metavar="N", help="Show the N most recent events",
    )
    group.add_argument(
        "--clear", action="store_true", help="Clear the telemetry log",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    tel = AgentTelemetry()

    if args.clear:
        tel.clear()
        print("Telemetry log cleared.")
        return

    if args.recent is not None:
        events = tel.read_recent(args.recent)
        if not events:
            print("No telemetry events found.")
            return
        print(f"Recent {len(events)} event(s):")
        for ev in events:
            print(f"  {ev.timestamp}  [{ev.event_type}]  {ev.agent_name}  {ev.tool_name or ev.file_path or ev.details}")
        return

    if args.agent:
        events = tel.read_events(args.agent)
        if not events:
            print(f"No telemetry events for agent '{args.agent}'.")
            return
        print(f"Events for agent '{args.agent}' ({len(events)}):")
        for ev in events:
            print(f"  {ev.timestamp}  [{ev.event_type}]  {ev.tool_name or ev.file_path or ev.details}")
        return

    # Default: summary
    s = tel.summary()
    total = s["total_events"]
    if total == 0:
        print("No telemetry events found.")
        return

    print(f"Telemetry Summary ({total} event{'s' if total != 1 else ''}):")
    if s["events_by_agent"]:
        print("\nBy Agent:")
        for agent, count in sorted(s["events_by_agent"].items(), key=lambda x: -x[1]):
            print(f"  {agent:<35} {count}")
    if s["events_by_type"]:
        print("\nBy Type:")
        for etype, count in sorted(s["events_by_type"].items(), key=lambda x: -x[1]):
            print(f"  {etype:<20} {count}")
    if s["files_read"]:
        print(f"\nFiles read:    {len(s['files_read'])}")
    if s["files_written"]:
        print(f"Files written: {len(s['files_written'])}")
