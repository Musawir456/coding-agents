import pytest

from coding_agents.agents import STEP_LIMIT_NOTE, Agent
from coding_agents.llm import Reply
from coding_agents.orchestrator import CODER, PLANNER, Orchestrator, parse_verdict
from coding_agents.tools import ToolBox


def text(s):
    return Reply([{"type": "text", "text": s}])


def tool(name, args, id="t1"):
    return Reply([{"type": "tool_use", "id": id, "name": name, "input": args}])


class ScriptedLLM:
    """Returns pre-baked replies in order and records every call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, messages, tools):
        self.calls.append(
            {"system": system, "messages": [dict(m) for m in messages], "tools": tools}
        )
        return self.replies.pop(0)


# ---------- tools ----------

def test_write_read_list(tmp_path):
    tb = ToolBox(tmp_path)
    assert "wrote" in tb.execute("write_file", {"path": "pkg/a.py", "content": "x = 1\n"})
    assert tb.execute("read_file", {"path": "pkg/a.py"}) == "x = 1\n"
    assert tb.execute("list_files", {}) == "pkg/a.py"


@pytest.mark.parametrize("bad", ["../evil.txt", "/etc/passwd", "a/../../evil.txt"])
def test_paths_cannot_escape_workspace(tmp_path, bad):
    tb = ToolBox(tmp_path / "ws")
    assert tb.execute("write_file", {"path": bad, "content": "x"}).startswith("ERROR")
    assert tb.execute("read_file", {"path": bad}).startswith("ERROR")
    assert not (tmp_path / "evil.txt").exists()


def test_command_allowlist_and_run(tmp_path):
    tb = ToolBox(tmp_path)
    assert "not allowed" in tb.execute("run_command", {"command": "rm -rf /"})
    assert "not allowed" in tb.execute("run_command", {"command": "curl http://example.com"})
    out = tb.execute("run_command", {"command": "python -c \"print('hi')\""})
    assert "exit code: 0" in out and "hi" in out


def test_command_timeout(tmp_path):
    tb = ToolBox(tmp_path, command_timeout=1)
    out = tb.execute("run_command", {"command": "python -c \"import time; time.sleep(5)\""})
    assert "timed out" in out


def test_unknown_tool_and_bad_args(tmp_path):
    tb = ToolBox(tmp_path)
    assert tb.execute("nope", {}).startswith("ERROR")
    assert tb.execute("read_file", {}).startswith("ERROR")


# ---------- agent loop ----------

def test_agent_runs_tool_then_answers(tmp_path):
    llm = ScriptedLLM([tool("write_file", {"path": "a.txt", "content": "hi"}), text("done")])
    agent = Agent(CODER, llm, ToolBox(tmp_path))
    assert agent.run("go") == "done"
    assert (tmp_path / "a.txt").read_text() == "hi"
    # second call must include the assistant tool_use and the matching tool_result
    msgs = llm.calls[1]["messages"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["content"][0]["tool_use_id"] == "t1"


def test_agent_cannot_use_tools_outside_its_role(tmp_path):
    llm = ScriptedLLM([tool("write_file", {"path": "a.txt", "content": "x"}), text("ok")])
    Agent(PLANNER, llm, ToolBox(tmp_path)).run("go")
    assert not (tmp_path / "a.txt").exists()
    result = llm.calls[1]["messages"][2]["content"][0]["content"]
    assert "not available" in result


def test_agent_stops_at_step_limit(tmp_path):
    llm = ScriptedLLM([tool("list_files", {}, id=f"t{i}") for i in range(5)])
    out = Agent(CODER, llm, ToolBox(tmp_path), max_steps=3).run("go")
    assert out == STEP_LIMIT_NOTE
    assert len(llm.calls) == 3


# ---------- orchestrator ----------

def test_parse_verdict():
    assert parse_verdict("looks good\nVERDICT: APPROVED")
    assert not parse_verdict("1. fix x\nVERDICT: CHANGES_REQUESTED")
    assert not parse_verdict("no verdict here")
    assert not parse_verdict("VERDICT: APPROVED\nactually no\nVERDICT: CHANGES_REQUESTED")


def test_orchestrator_approves_first_round(tmp_path):
    llm = ScriptedLLM(
        [
            text("1. write add.py"),  # planner
            tool("write_file", {"path": "add.py", "content": "def add(a, b): return a + b\n"}),
            text("wrote add.py"),  # coder
            text("All good.\nVERDICT: APPROVED"),  # reviewer
        ]
    )
    result = Orchestrator(llm, str(tmp_path)).run("add function")
    assert result.approved and result.rounds == 1
    assert (tmp_path / "add.py").exists()


def test_reviewer_feedback_reaches_coder_on_next_round(tmp_path):
    llm = ScriptedLLM(
        [
            text("plan"),
            text("first attempt"),
            text("1. handle empty input\nVERDICT: CHANGES_REQUESTED"),
            text("fixed it"),
            text("VERDICT: APPROVED"),
        ]
    )
    result = Orchestrator(llm, str(tmp_path)).run("task")
    assert result.approved and result.rounds == 2
    second_coder_prompt = llm.calls[3]["messages"][0]["content"]
    assert "handle empty input" in second_coder_prompt


def test_orchestrator_gives_up_after_max_rounds(tmp_path):
    bad = "VERDICT: CHANGES_REQUESTED"
    llm = ScriptedLLM([text("plan"), text("c1"), text(bad), text("c2"), text(bad)])
    result = Orchestrator(llm, str(tmp_path), max_rounds=2).run("task")
    assert not result.approved and result.rounds == 2 and len(result.history) == 2


def test_events_are_emitted(tmp_path):
    events = []
    llm = ScriptedLLM([text("plan"), text("coded"), text("VERDICT: APPROVED")])
    Orchestrator(llm, str(tmp_path), on_event=lambda *e: events.append(e)).run("t")
    assert ("orchestrator", "round", "round 1/3") in events
    assert {e[0] for e in events} >= {"planner", "coder", "reviewer"}
