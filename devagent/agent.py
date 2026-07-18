from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .tools import TOOL_DEFINITIONS, ToolExecutor
from .checks import ProjectChecker
from .browser_check import BrowserChecker
from .workspace import WorkspaceError, WorkspaceManager

if TYPE_CHECKING:
    from .ollama import OllamaClient


SYSTEM_PROMPT = """You are DevAgent, a careful senior developer operating on one local project.
For an existing project, list files first and read every relevant source file before editing or validating.
If list_files returns zero files, the project is new: do not call read_file or search_text. Create the required files with write_file immediately.
Never invent file contents you have not read.
Prefer replace_text for small edits and write_file for new files or full rewrites.
For repeated JSON fields use remove_json_key. If an exact replacement fails, read again and use write_file with complete corrected content.
Only create UTF-8 text files. For imagery use CSS, inline SVG, or an .svg file; never write fake binary data or depend on remote assets because browser checks run without external network access.
Keep HTML, CSS, JavaScript and JSON consistent. If JavaScript fetches JSON, verify its exact shape.
For a React/Vite project, use only package.json, index.html and source files. Supported packages are vite, react and react-dom. Do not create vite.config.*, package-lock.json or node_modules; the managed runtime supplies dependencies and ignores package scripts.
Fully implement every requested feature. Never leave placeholder comments, empty panels, fake tabs, or controls without working behavior.
Use the user's language for visible interface text unless they explicitly request another language.
When the user asks for a modern or premium design, create a complete visual system with responsive layouts, deliberate typography, color variables, layered backgrounds, polished cards, and hover/focus states rather than a bare demo.
In React, implement tabs and other interactive controls with component state and visible content changes.
Never put literal backslash-n text between JSX or HTML elements. Tool content must contain actual newline characters.
Never remove a stylesheet or script reference merely to silence a missing-asset issue. Keep source assets connected to HTML.
Before finishing, call run_project_checks and fix every issue that is in scope. After it passes, call browser_check and fix every runtime or layout issue it reports.
After validation reports an issue, edit the SOURCE file named by the issue. Deleting a missing target does not fix references to it.
Never repeat a tool call that failed or returned the same information. Choose a different action.
Never ask the user for a file path that can be discovered with list_files. Recover from tool errors autonomously.
When the task is complete, answer briefly in the user's language and mention what changed.

If native tool calling is unavailable, request exactly one tool using JSON and no Markdown:
{"name":"tool_name","arguments":{"argument":"value"}}
Do not expose chain-of-thought. Use tools for actions and a concise final answer for the user.
"""


@dataclass
class AgentResult:
    message: str
    files: list[dict[str, Any]]
    issues: list[dict[str, str]]
    steps: list[dict[str, Any]]
    screenshots: list[str]


class AgentService:
    def __init__(
        self,
        ollama: "OllamaClient",
        workspace: WorkspaceManager,
        max_steps: int = 16,
        browser_checker: BrowserChecker | None = None,
    ):
        self.ollama = ollama
        self.workspace = workspace
        self.max_steps = max_steps
        self.browser_checker = browser_checker or BrowserChecker(workspace, workspace.checkpoints_root.parent / "artifacts")

    def run(self, slug: str, history: list[dict[str, Any]], model: str) -> AgentResult:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in history[-12:]:
            if item.get("role") in {"user", "assistant"}:
                messages.append({"role": item["role"], "content": str(item.get("content", ""))})

        quality_profile = self._quality_profile(history)
        executor = ToolExecutor(self.workspace, slug, self.browser_checker, quality_profile)
        initial_source_files = {
            str(item["path"]) for item in self.workspace.list_files(slug)
            if str(item["path"]).endswith((".html", ".css", ".js", ".jsx", ".tsx", ".json"))
            or str(item["path"]) == "package.json"
        }
        steps: list[dict[str, Any]] = []
        changed: dict[str, dict[str, Any]] = {}
        final_message = ""
        call_counts: dict[str, int] = {}
        read_files: set[str] = set()
        mutated_since_validation = False
        last_validation: str | None = None
        repeated_error_count = 0
        last_error_signature: str | None = None
        unresolved_tool_error = False
        project_checks_completed = False
        browser_checks_completed = False
        browser_check_attempted = False
        browser_issues: list[dict[str, str]] = []
        screenshots: list[str] = []

        for step_number in range(1, self.max_steps + 1):
            model_timeout = 900 if "32b" in model.casefold() else 300
            assistant = self.ollama.chat(
                model, messages, TOOL_DEFINITIONS, num_predict=4096, timeout=model_timeout, num_ctx=8192
            )
            calls = self._tool_calls(assistant)
            if not calls:
                candidate = str(assistant.get("content", "")).strip()
                if candidate and unresolved_tool_error:
                    messages.append(assistant)
                    messages.append({
                        "role": "user",
                        "content": "The last tool call failed, so the task is not complete. Call list_files if paths are unknown, recover with a different tool, and only then finish.",
                    })
                    continue
                if candidate and self.workspace.list_files(slug) and not project_checks_completed:
                    messages.append(assistant)
                    messages.append({
                        "role": "user",
                        "content": "Before the final answer you must call run_project_checks. Finish only when it returns passed=true.",
                    })
                    continue
                if candidate and self.workspace.list_files(slug) and not browser_checks_completed:
                    messages.append(assistant)
                    messages.append({
                        "role": "user",
                        "content": "Static checks passed. Before the final answer call browser_check. Finish only when it returns passed=true.",
                    })
                    continue
                final_message = candidate
                if final_message:
                    break
                messages.append({"role": "user", "content": "Continue using a tool or provide the final answer."})
                continue

            messages.append(assistant)
            for call in calls:
                name = call["name"]
                arguments = call["arguments"]
                record: dict[str, Any] = {
                    "step": step_number, "tool": name, "arguments": self._safe_arguments(name, arguments), "status": "ok"
                }
                try:
                    signature = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True)
                    call_counts[signature] = call_counts.get(signature, 0) + 1
                    check_tools = {"validate_project", "run_project_checks", "browser_check"}
                    guarded_tools = {"write_file", "replace_text", "delete_file", "remove_json_key"}
                    if name in guarded_tools and call_counts[signature] > 2:
                        raise WorkspaceError("Repeated tool call blocked. Choose a different tool or arguments.")
                    if name in check_tools and self.workspace.list_files(slug) and not read_files and not changed:
                        raise WorkspaceError("Inspect relevant source files with read_file before validating an existing project.")
                    if name in check_tools and quality_profile == "polished":
                        unread = sorted(initial_source_files - read_files)
                        if unread:
                            raise WorkspaceError("Read every source file before checking a full redesign. Unread: " + ", ".join(unread))
                    if name in {"validate_project", "run_project_checks"} and last_validation is not None and not mutated_since_validation:
                        raise WorkspaceError("Repeated validation blocked because no files changed. Read and edit the source files from the issues.")
                    if name == "browser_check" and not project_checks_completed:
                        raise WorkspaceError("Run project checks successfully before browser_check.")
                    if name == "browser_check" and browser_check_attempted and not mutated_since_validation:
                        raise WorkspaceError("Repeated browser check blocked because no files changed. Fix the reported source files first.")

                    result = executor.execute(name, arguments)
                    if name == "read_file" and isinstance(arguments.get("path"), str):
                        read_files.add(arguments["path"])
                    write = result.get("write")
                    if isinstance(write, dict) and write.get("path"):
                        changed[str(write["path"])] = write
                        mutated_since_validation = True
                        project_checks_completed = False
                        browser_checks_completed = False
                        browser_check_attempted = False
                        browser_issues = []
                        screenshots = []
                    deleted = result.get("delete")
                    if isinstance(deleted, dict) and deleted.get("path"):
                        changed[str(deleted["path"])] = {**deleted, "deleted": True, "bytes_written": 0}
                        mutated_since_validation = True
                        project_checks_completed = False
                        browser_checks_completed = False
                        browser_check_attempted = False
                        browser_issues = []
                        screenshots = []
                    if name in {"validate_project", "run_project_checks"}:
                        check_issues = result.get("issues", result.get("check_result", {}).get("issues", []))
                        last_validation = json.dumps(check_issues, ensure_ascii=False, sort_keys=True)
                        mutated_since_validation = False
                    if name == "run_project_checks":
                        project_checks_completed = bool(result.get("check_result", {}).get("passed"))
                    if name == "browser_check":
                        browser_result = result.get("browser_result", {})
                        browser_check_attempted = True
                        browser_checks_completed = bool(browser_result.get("passed"))
                        browser_issues = list(browser_result.get("issues", []))
                        screenshots = list(browser_result.get("screenshots", []))
                        mutated_since_validation = False
                        if browser_checks_completed:
                            if changed:
                                final_message = "Готово. Изменены файлы: " + ", ".join(sorted(changed)) + ". Статические и браузерные проверки пройдены."
                            else:
                                final_message = "Проверка завершена: статических и браузерных ошибок не найдено."
                    record["result"] = self._summary(result)
                    tool_content = json.dumps(
                        {"ok": True, **result, "next_action": self._next_action(name, result)}, ensure_ascii=False
                    )
                    repeated_error_count = 0
                    last_error_signature = None
                    unresolved_tool_error = False
                except (WorkspaceError, ValueError, TypeError) as exc:
                    record["status"] = "error"
                    record["result"] = str(exc)
                    project_is_empty = not self.workspace.list_files(slug)
                    corrective_action = (
                        "The exact edit failed. Read the current file and use write_file with the complete corrected content, "
                        "or remove_json_key for a repeated JSON key. Do not repeat the same replacement."
                        if name == "replace_text"
                        else (
                            "Call list_files now, read the relevant source files yourself, then run the checks again. Do not ask the user for paths."
                            if name in check_tools
                            else (
                                "The project is empty. Do not call read_file again. Create package.json, index.html and the required source files with write_file."
                                if name == "read_file" and project_is_empty
                                else "Do not repeat this call. Use list_files, then read an existing relevant file or make a different corrective edit."
                            )
                        )
                    )
                    tool_content = json.dumps({
                        "ok": False,
                        "error": str(exc),
                        "next_action": corrective_action,
                    }, ensure_ascii=False)
                    error_signature = json.dumps(
                        {"tool": name, "arguments": arguments, "error": str(exc)}, ensure_ascii=False, sort_keys=True
                    )
                    repeated_error_count = repeated_error_count + 1 if error_signature == last_error_signature else 1
                    last_error_signature = error_signature
                    unresolved_tool_error = True
                steps.append(record)
                messages.append({"role": "tool", "tool_name": name, "content": tool_content})
                if repeated_error_count >= 3:
                    final_message = "Остановлено после трёх повторяющихся ошибок инструментов. Проверьте журнал действий."
                    break
                if final_message:
                    break
            if final_message:
                break

        final_static = ProjectChecker(self.workspace).run(slug)
        if final_static["passed"] and not browser_checks_completed and self.workspace.list_files(slug):
            final_browser = self.browser_checker.run(slug, quality_profile)
            browser_issues = list(final_browser.get("issues", []))
            screenshots = list(final_browser.get("screenshots", []))
        issues = final_static["issues"] + browser_issues
        if not final_message:
            final_message = f"Остановлено после {self.max_steps} шагов. Проверьте журнал действий."
        return AgentResult(
            message=final_message,
            files=list(changed.values()),
            issues=issues,
            steps=steps,
            screenshots=screenshots,
        )

    @staticmethod
    def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        native = message.get("tool_calls") or []
        for call in native:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = function.get("name")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if isinstance(name, str) and isinstance(arguments, dict):
                parsed.append({"name": name, "arguments": arguments})
        if parsed:
            return parsed

        content = str(message.get("content", "")).strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        decoder = json.JSONDecoder()
        position = 0
        fallbacks: list[dict[str, Any]] = []
        while position < len(content):
            while position < len(content) and content[position].isspace():
                position += 1
            if position >= len(content):
                break
            try:
                value, position = decoder.raw_decode(content, position)
            except json.JSONDecodeError:
                return []
            if not (
                isinstance(value, dict)
                and isinstance(value.get("name"), str)
                and isinstance(value.get("arguments", {}), dict)
            ):
                return []
            fallbacks.append({"name": value["name"], "arguments": value.get("arguments", {})})
        return fallbacks

    @staticmethod
    def _safe_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        safe = dict(arguments)
        for key in ("content", "old", "new"):
            if key in safe:
                value = str(safe[key])
                safe[key] = f"<{len(value)} chars>"
        return safe

    @staticmethod
    def _quality_profile(history: list[dict[str, Any]]) -> str:
        recent_user_text = " ".join(
            str(item.get("content", "")).casefold() for item in history[-6:] if item.get("role") == "user"
        )
        markers = ("современн", "премиальн", "супер", "modern", "premium", "выразительн", "качественн")
        return "polished" if any(marker in recent_user_text for marker in markers) else "standard"

    @staticmethod
    def _summary(result: dict[str, Any]) -> str:
        if "write" in result:
            write = result["write"]
            return f"Saved {write['path']} ({write['bytes_written']} bytes)"
        if "delete" in result:
            return f"Deleted {result['delete']['path']} (checkpoint saved)"
        if "files" in result:
            return f"Found {len(result['files'])} files"
        if "matches" in result:
            return f"Found {len(result['matches'])} matches"
        if "issues" in result:
            return f"Validation found {len(result['issues'])} issues"
        if "check_result" in result:
            check_result = result["check_result"]
            return f"Project checks found {len(check_result['issues'])} issues"
        if "browser_result" in result:
            browser_result = result["browser_result"]
            return f"Browser check found {len(browser_result['issues'])} issues in {len(browser_result['viewports'])} viewports"
        if "content" in result:
            return f"Read lines {result['start_line']}-{result['end_line']} of {result['total_lines']}"
        return "Done"

    @staticmethod
    def _next_action(name: str, result: dict[str, Any]) -> str:
        if name == "validate_project" and result.get("issues"):
            return "Read each SOURCE file named in the issues, edit its broken references or structure, then validate again."
        if name == "run_project_checks" and result.get("check_result", {}).get("issues"):
            return "Read and fix every file named by the checks, then run project checks again."
        if name == "run_project_checks" and result.get("check_result", {}).get("passed"):
            return "Static checks passed. Run browser_check before finishing."
        if name == "browser_check" and result.get("browser_result", {}).get("issues"):
            messages = " | ".join(issue.get("message", "") for issue in result["browser_result"]["issues"])
            hints: list[str] = []
            if "Tabs do not react" in messages or "Tabs need exactly one non-empty visible tabpanel" in messages:
                hints.append("Implement tabs with React useState and render exactly one active element with role=\"tabpanel\", non-empty text, and hidden/unmounted inactive panels; tabs must derive aria-selected and update active id on click.")
            if "horizontal overflow" in messages:
                hints.append("Read the CSS and fix mobile overflow with min-width:0, wrapping grids/flex rows, max-width:100%, and a mobile media query.")
            if "Polished design requested" in messages:
                hints.append("Expand the page with at least three meaningful sections and a real design system: CSS variables, responsive grids, rich cards, layered surfaces, and hover/focus states.")
            if "add at least three meaningful page sections" in messages:
                hints.append("Keep at least three semantic <section> elements mounted at the same time outside the conditional tabpanel, for example gaming zones, pricing, and contact/CTA sections.")
            return "Browser issues: " + messages + " Next: " + " ".join(hints or ["Read and fix the responsible source files."]) + " Then rerun project checks and browser_check."
        if name == "delete_file":
            return "Read and update any HTML, CSS, JavaScript or JSON files that referenced the deleted path."
        if name in {"write_file", "replace_text", "remove_json_key"}:
            return "Inspect other dependent files for consistency; validate only after all related edits are complete."
        if name == "list_files":
            if not result.get("files"):
                return "The project is empty. Do not call read_file or search_text. Create all required project files now with write_file."
            return "Read the relevant source files before deciding edits."
        return "Continue with the next distinct action needed for the task."
