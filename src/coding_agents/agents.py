"""A single tool-using agent: one role, one tool subset, one tool-use loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .llm import LLM
from .tools import ToolBox

EventHandler = Callable[[str, str, str], None]  # (agent name, kind, detail)

STEP_LIMIT_NOTE = "[stopped: step limit reached before the agent finished]"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    system: str
    tools: tuple[str, ...]


class Agent:
    def __init__(
        self,
        spec: AgentSpec,
        llm: LLM,
        toolbox: ToolBox,
        max_steps: int = 20,
        on_event: EventHandler | None = None,
    ) -> None:
        self.spec = spec
        self._llm = llm
        self._toolbox = toolbox
        self._max_steps = max_steps
        self._emit = on_event or (lambda *_: None)

    def run(self, prompt: str) -> str:
        """Run the loop until the model answers without calling tools. Returns its text."""
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tool_specs = self._toolbox.specs(self.spec.tools)

        for _ in range(self._max_steps):
            reply = self._llm.complete(self.spec.system, messages, tool_specs)
            messages.append({"role": "assistant", "content": reply.content})
            calls = reply.tool_calls
            if not calls:
                self._emit(self.spec.name, "done", reply.text)
                return reply.text

            results = []
            for call in calls:
                self._emit(self.spec.name, "tool", f"{call.name} {call.input}")
                if call.name not in self.spec.tools:
                    out = f"ERROR: tool '{call.name}' is not available to the {self.spec.name}"
                else:
                    out = self._toolbox.execute(call.name, call.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": out}
                )
            messages.append({"role": "user", "content": results})

        self._emit(self.spec.name, "done", STEP_LIMIT_NOTE)
        return STEP_LIMIT_NOTE
