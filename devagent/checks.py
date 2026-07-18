from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .validation import validate_project
from .workspace import WorkspaceError, WorkspaceManager


CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
CSS_TRAILING_COMMA = re.compile(r",\s*}")


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


class SafeCommandRunner:
    """Runs only argument arrays assembled by trusted application code."""

    def __init__(self, timeout_seconds: int = 15, output_limit: int = 4000):
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    def run(self, arguments: list[str], cwd: Path) -> CommandResult:
        if not arguments or arguments[0] not in {"node"}:
            raise WorkspaceError("Command is not in the safe checker allowlist")
        try:
            process = subprocess.run(
                arguments,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(f"Checker timed out after {self.timeout_seconds} seconds") from exc
        return CommandResult(
            command=" ".join(arguments),
            returncode=process.returncode,
            stdout=process.stdout[-self.output_limit :],
            stderr=process.stderr[-self.output_limit :],
        )


class ProjectChecker:
    def __init__(self, workspace: WorkspaceManager, runner: SafeCommandRunner | None = None):
        self.workspace = workspace
        self.runner = runner or SafeCommandRunner()

    def run(self, slug: str) -> dict[str, Any]:
        root = self.workspace.project_root(slug)
        issues: list[dict[str, str]] = list(validate_project(self.workspace, slug))
        checks: list[dict[str, Any]] = []
        files = [str(item["path"]) for item in self.workspace.list_files(slug)]

        for path in files:
            suffix = Path(path).suffix.lower()
            if suffix == ".py":
                issues.extend(self._check_python(slug, path))
                checks.append({"kind": "python_syntax", "file": path})
            elif suffix == ".css":
                issues.extend(self._check_css(slug, path))
                checks.append({"kind": "css_syntax", "file": path})
            elif suffix in {".js", ".mjs", ".cjs"}:
                result_issues, result = self._check_javascript(slug, path, root)
                issues.extend(result_issues)
                checks.append({"kind": "javascript_syntax", "file": path, "returncode": result.returncode if result else None})

        return {"passed": not issues, "issues": issues, "checks": checks}

    def _check_python(self, slug: str, path: str) -> list[dict[str, str]]:
        content = self.workspace.read_text(slug, path)
        try:
            compile(content, path, "exec")
            return []
        except SyntaxError as exc:
            return [{
                "file": path,
                "code": "python_syntax",
                "message": f"Python syntax error at line {exc.lineno}: {exc.msg}",
            }]

    def _check_css(self, slug: str, path: str) -> list[dict[str, str]]:
        content = CSS_COMMENT.sub("", self.workspace.read_text(slug, path))
        issues: list[dict[str, str]] = []
        if content.count("{") != content.count("}"):
            issues.append({"file": path, "code": "css_unbalanced_braces", "message": "CSS has unbalanced braces"})
        if CSS_TRAILING_COMMA.search(content):
            issues.append({"file": path, "code": "css_trailing_comma", "message": "CSS declaration ends with a dangling comma"})
        return issues

    def _check_javascript(
        self, slug: str, path: str, root: Path
    ) -> tuple[list[dict[str, str]], CommandResult | None]:
        if shutil.which("node") is None:
            return ([{"file": path, "code": "checker_unavailable", "message": "Node.js checker is unavailable"}], None)
        absolute = self.workspace.resolve(slug, path, must_exist=True)
        result = self.runner.run(["node", "--check", str(absolute)], root)
        if result.returncode == 0:
            return [], result
        message = (result.stderr or result.stdout or "JavaScript syntax check failed").replace(str(root), ".")
        return ([{"file": path, "code": "javascript_syntax", "message": message[-1200:]}], result)

