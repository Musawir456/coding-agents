"""Workspace-confined tools the agents can call.

NOTE: this confines *file access* to one directory and restricts which programs may be
launched, but it is not a security sandbox. A program such as `python` can still do
anything the current user can. Run the tool inside Docker/a VM for untrusted tasks.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

IGNORED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".ruff_cache",
}
ALLOWED_COMMANDS = {"python", "python3", "pytest", "ruff", "node", "npm"}
MAX_READ_CHARS = 20_000
MAX_OUTPUT_CHARS = 8_000
MAX_LIST_ENTRIES = 200


class ToolError(Exception):
    """A tool call failed in a way the model should be told about."""


TOOL_SPECS: dict[str, dict] = {
    "list_files": {
        "name": "list_files",
        "description": "List all files in the workspace (relative paths).",
        "input_schema": {"type": "object", "properties": {}},
    },
    "read_file": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path"}},
            "required": ["path"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": "Create or overwrite a file in the workspace. Parent folders are created.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Full file contents"},
            },
            "required": ["path", "content"],
        },
    },
    "run_command": {
        "name": "run_command",
        "description": (
            "Run a command inside the workspace (no shell). Allowed programs: "
            + ", ".join(sorted(ALLOWED_COMMANDS))
            + ". Example: `python -m pytest -q`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


class ToolBox:
    def __init__(self, root: str | Path, command_timeout: int = 60) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.command_timeout = command_timeout

    def specs(self, names: tuple[str, ...]) -> list[dict]:
        return [TOOL_SPECS[n] for n in names]

    def _resolve(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise ToolError("path must be a non-empty string")
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise ToolError(f"path escapes the workspace: {rel}")
        return path

    def execute(self, name: str, args: dict) -> str:
        """Run a tool and return its output; failures become `ERROR: ...` strings."""
        try:
            if name == "list_files":
                return self._list_files()
            if name == "read_file":
                return self._read_file(args.get("path"))
            if name == "write_file":
                return self._write_file(args.get("path"), args.get("content"))
            if name == "run_command":
                return self._run_command(args.get("command"))
            raise ToolError(f"unknown tool: {name}")
        except ToolError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001 - report any failure back to the model
            return f"ERROR: {type(exc).__name__}: {exc}"

    def _list_files(self) -> str:
        files = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and not (IGNORED_DIRS & set(p.relative_to(self.root).parts))
        )
        if not files:
            return "(workspace is empty)"
        extra = len(files) - MAX_LIST_ENTRIES
        shown = files[:MAX_LIST_ENTRIES]
        return "\n".join(shown) + (f"\n...and {extra} more" if extra > 0 else "")

    def _read_file(self, rel: str) -> str:
        path = self._resolve(rel)
        if not path.is_file():
            raise ToolError(f"no such file: {rel}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"not a UTF-8 text file: {rel}") from exc
        return _truncate(text, MAX_READ_CHARS)

    def _write_file(self, rel: str, content: str) -> str:
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        path = self._resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {rel}"

    def _run_command(self, command: str) -> str:
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command must be a non-empty string")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ToolError(f"cannot parse command: {exc}") from exc
        if argv[0] not in ALLOWED_COMMANDS:
            raise ToolError(
                f"program not allowed: {argv[0]} (allowed: {', '.join(sorted(ALLOWED_COMMANDS))})"
            )
        if argv[0] in ("python", "python3"):
            argv[0] = sys.executable
        try:
            proc = subprocess.run(
                argv,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {self.command_timeout}s"
        except FileNotFoundError:
            raise ToolError(f"program not installed: {argv[0]}") from None
        output = (proc.stdout + proc.stderr).strip()
        return _truncate(f"exit code: {proc.returncode}\n{output}", MAX_OUTPUT_CHARS)
