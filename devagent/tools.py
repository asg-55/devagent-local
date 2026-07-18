from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from .validation import validate_project
from .checks import ProjectChecker
from .browser_check import BrowserChecker
from .workspace import WorkspaceError, WorkspaceManager


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List every file in the current project with its byte size.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a UTF-8 text file, optionally selecting a line range.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "search_text", "description": "Search for literal text in UTF-8 project files.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "path": {"type": "string", "description": "Optional file or folder prefix"},
            "case_sensitive": {"type": "boolean"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or fully replace one UTF-8 text file. Binary files are rejected.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "replace_text", "description": "Replace one exact, unique text fragment in an existing file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
        }, "required": ["path", "old", "new"]},
    }},
    {"type": "function", "function": {
        "name": "delete_file", "description": "Delete one project file after an automatic recoverable checkpoint.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "remove_json_key", "description": "Recursively remove every occurrence of one object key from a JSON file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "key": {"type": "string"},
        }, "required": ["path", "key"]},
    }},
    {"type": "function", "function": {
        "name": "run_project_checks", "description": "Run safe syntax and consistency checks for the entire project.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "browser_check",
        "description": "Render index.html in headless Chromium at desktop and mobile sizes, capture screenshots, and report runtime or layout failures.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
]


class ToolExecutor:
    def __init__(
        self,
        workspace: WorkspaceManager,
        slug: str,
        browser_checker: BrowserChecker | None = None,
        quality_profile: str = "standard",
    ):
        self.workspace = workspace
        self.slug = slug
        self.browser_checker = browser_checker or BrowserChecker(workspace, workspace.checkpoints_root.parent / "artifacts")
        self.quality_profile = quality_profile

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_files":
            return {"files": self.workspace.list_files(self.slug)}
        if name == "read_file":
            return self._read_file(arguments)
        if name == "search_text":
            return self._search_text(arguments)
        if name == "write_file":
            result = self.workspace.write_text(self.slug, self._string(arguments, "path"), self._string(arguments, "content"))
            return {"write": asdict(result)}
        if name == "replace_text":
            result = self.workspace.replace_text(
                self.slug, self._string(arguments, "path"), self._string(arguments, "old"), self._string(arguments, "new")
            )
            return {"write": asdict(result)}
        if name == "delete_file":
            result = self.workspace.delete_file(self.slug, self._string(arguments, "path"))
            return {"delete": asdict(result)}
        if name == "remove_json_key":
            path = self._string(arguments, "path")
            key = self._string(arguments, "key")
            try:
                data = json.loads(self.workspace.read_text(self.slug, path))
            except json.JSONDecodeError as exc:
                raise WorkspaceError(f"Invalid JSON: {exc.msg}") from exc
            removed = self._remove_key(data, key)
            if removed == 0:
                raise WorkspaceError(f"JSON key not found: {key}")
            result = self.workspace.write_text(self.slug, path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return {"write": asdict(result), "removed": removed}
        if name == "validate_project":
            return {"issues": validate_project(self.workspace, self.slug)}
        if name == "run_project_checks":
            return {"check_result": ProjectChecker(self.workspace).run(self.slug)}
        if name == "browser_check":
            return {"browser_result": self.browser_checker.run(self.slug, self.quality_profile)}
        raise WorkspaceError(f"Unknown tool: {name}")

    @staticmethod
    def _string(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str):
            raise WorkspaceError(f"Tool argument '{key}' must be a string")
        return value

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._string(arguments, "path")
        lines = self.workspace.read_text(self.slug, path).splitlines()
        start = max(1, int(arguments.get("start_line", 1)))
        end = min(len(lines), int(arguments.get("end_line", min(len(lines), start + 399))))
        if end < start:
            raise WorkspaceError("end_line must not be smaller than start_line")
        selected = "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))
        return {"path": path, "start_line": start, "end_line": end, "total_lines": len(lines), "content": selected}

    def _search_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = self._string(arguments, "query")
        if not query:
            raise WorkspaceError("Search query cannot be empty")
        prefix = str(arguments.get("path", "")).replace("\\", "/").strip("/")
        case_sensitive = bool(arguments.get("case_sensitive", False))
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        for item in self.workspace.list_files(self.slug):
            path = str(item["path"])
            if prefix and path != prefix and not path.startswith(prefix + "/"):
                continue
            try:
                lines = self.workspace.read_text(self.slug, path).splitlines()
            except WorkspaceError:
                continue
            for number, line in enumerate(lines, 1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append({"path": path, "line": number, "text": line[:300]})
                    if len(matches) >= 50:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    @classmethod
    def _remove_key(cls, value: Any, key: str) -> int:
        removed = 0
        if isinstance(value, dict):
            if key in value:
                del value[key]
                removed += 1
            for child in value.values():
                removed += cls._remove_key(child, key)
        elif isinstance(value, list):
            for child in value:
                removed += cls._remove_key(child, key)
        return removed
