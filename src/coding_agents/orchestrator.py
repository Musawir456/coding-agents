"""Planner -> Coder -> Reviewer loop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .agents import Agent, AgentSpec, EventHandler
from .llm import LLM
from .tools import ToolBox

PLANNER = AgentSpec(
    name="planner",
    tools=("list_files", "read_file"),
    system=(
        "You are the Planner in a team of coding agents. Inspect the workspace if needed, then "
        "produce a short, numbered implementation plan: files to create or change, key design "
        "decisions, and how the result will be verified (tests). Do not write code. "
        "Keep the plan under 25 lines."
    ),
)

CODER = AgentSpec(
    name="coder",
    tools=("list_files", "read_file", "write_file", "run_command"),
    system=(
        "You are the Coder in a team of coding agents. Implement the task by following the plan. "
        "Write complete files with the tools (never paste code in your reply), include tests, run "
        "them with `python -m pytest -q`, and fix failures before finishing. If the Reviewer sent "
        "feedback, address every point. Finish with a 2-3 sentence summary of what you did."
    ),
)

REVIEWER = AgentSpec(
    name="reviewer",
    tools=("list_files", "read_file", "run_command"),
    system=(
        "You are the Reviewer in a team of coding agents. You cannot edit files. Read the code, "
        "run the tests with `python -m pytest -q`, and check it against the task: correctness, "
        "edge cases, tests, and clarity. Be concrete and brief. End your reply with exactly one "
        "final line: `VERDICT: APPROVED` if the task is fully and correctly done and tests pass, "
        "otherwise `VERDICT: CHANGES_REQUESTED` preceded by a numbered list of required fixes."
    ),
)

_VERDICT = re.compile(r"^\s*VERDICT:\s*(APPROVED|CHANGES_REQUESTED)\s*$", re.MULTILINE)


def parse_verdict(text: str) -> bool:
    """True only if the *last* VERDICT line says APPROVED. Missing verdict means not approved."""
    matches = _VERDICT.findall(text)
    return bool(matches) and matches[-1] == "APPROVED"


@dataclass
class RunResult:
    approved: bool
    rounds: int
    plan: str
    coder_summary: str
    review: str
    history: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        llm: LLM,
        workspace: str,
        max_rounds: int = 3,
        max_steps: int = 20,
        on_event: EventHandler | None = None,
    ) -> None:
        self._toolbox = ToolBox(workspace)
        self._max_rounds = max_rounds
        make = lambda spec: Agent(spec, llm, self._toolbox, max_steps, on_event)  # noqa: E731
        self._planner, self._coder, self._reviewer = make(PLANNER), make(CODER), make(REVIEWER)
        self._emit = on_event or (lambda *_: None)

    def run(self, task: str) -> RunResult:
        plan = self._planner.run(f"Task:\n{task}")
        summary = review = feedback = ""
        history: list[str] = []
        approved = False
        rounds = 0

        for rounds in range(1, self._max_rounds + 1):
            self._emit("orchestrator", "round", f"round {rounds}/{self._max_rounds}")
            prompt = f"Task:\n{task}\n\nPlan:\n{plan}"
            if feedback:
                prompt += f"\n\nReviewer feedback to address:\n{feedback}"
            summary = self._coder.run(prompt)
            review = self._reviewer.run(
                f"Task:\n{task}\n\nCoder's summary:\n{summary}\n\nReview the workspace now."
            )
            history.append(review)
            if parse_verdict(review):
                approved = True
                break
            feedback = review

        return RunResult(approved, rounds, plan, summary, review, history)
