"""Command-line entry point: `coding-agents "build a todo CLI" --workspace ./out`."""

from __future__ import annotations

import argparse
import os
import sys

from .llm import ClaudeLLM
from .orchestrator import Orchestrator

ICONS = {"planner": "🧭", "coder": "🛠️ ", "reviewer": "🔍", "orchestrator": "🔁"}


def _print_event(agent: str, kind: str, detail: str) -> None:
    icon = ICONS.get(agent, "•")
    if kind == "tool":
        print(f"  {icon} {agent}: {detail[:120]}")
    elif kind == "round":
        print(f"\n{icon} {detail}")
    elif kind == "done":
        first = detail.strip().splitlines()[0] if detail.strip() else "(no output)"
        print(f"{icon} {agent} finished: {first[:120]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coding-agents",
        description="Planner, Coder and Reviewer agents build your task in a workspace folder.",
    )
    parser.add_argument("task", help="What to build, in plain language")
    parser.add_argument("-w", "--workspace", default="./workspace", help="Folder agents work in")
    default_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    parser.add_argument("-m", "--model", default=default_model)
    parser.add_argument("--max-rounds", type=int, default=3, help="Max coder/reviewer rounds")
    parser.add_argument("--max-steps", type=int, default=20, help="Max tool steps per agent turn")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: set the ANTHROPIC_API_KEY environment variable.", file=sys.stderr)
        return 2

    orch = Orchestrator(
        ClaudeLLM(model=args.model),
        args.workspace,
        max_rounds=args.max_rounds,
        max_steps=args.max_steps,
        on_event=_print_event,
    )
    result = orch.run(args.task)

    print("\n" + "=" * 60)
    print("APPROVED" if result.approved else "NOT APPROVED (round limit reached)")
    print(f"Rounds used: {result.rounds}   Workspace: {os.path.abspath(args.workspace)}")
    print("\nFinal review:\n" + result.review)
    return 0 if result.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
