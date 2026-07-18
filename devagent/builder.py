from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .workspace import WorkspaceManager
from .validation import validate_project

if TYPE_CHECKING:
    from .ollama import OllamaClient


def extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("The model returned an invalid project plan") from exc
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        raise ValueError("The project plan does not contain a files list")
    return value


def extract_content(text: str) -> str:
    match = re.search(r"<<<CONTENT>>>\s*(.*?)\s*<<<END>>>", text, re.DOTALL)
    if match:
        return match.group(1)
    fenced = re.fullmatch(r"\s*```[^\n]*\n(.*?)\n```\s*", text, re.DOTALL)
    return fenced.group(1) if fenced else text.strip()


class BuilderService:
    """Reliable transitional builder. A tool-calling loop will replace this in phase 2."""

    def __init__(self, ollama: OllamaClient, workspace: WorkspaceManager, max_context_chars: int):
        self.ollama = ollama
        self.workspace = workspace
        self.max_context_chars = max_context_chars

    def run(self, slug: str, request: str, model: str) -> dict[str, Any]:
        context = self.workspace.project_context(slug, self.max_context_chars)
        plan_prompt = f"""You are planning a small software project update.
User request: {request}

Current project files and contents:
{context}

Return ONLY valid JSON in this schema:
{{"summary":"short plan","files":[{{"path":"relative/path.ext","purpose":"what to create or change"}}]}}
Include only files that must be created or changed. Use safe relative paths. Do not delete files.
"""
        plan = extract_json(self.ollama.generate(model, plan_prompt, num_predict=900, timeout=120))
        file_specs = plan["files"][:20]
        generated: list[tuple[str, str]] = []
        generated_context = ""

        for spec in file_specs:
            path = str(spec.get("path", "")).strip()
            if path:
                self.workspace.resolve(slug, path)

        for spec in file_specs:
            path = str(spec.get("path", "")).strip()
            purpose = str(spec.get("purpose", "")).strip()
            if not path:
                continue
            existing = "(new file)"
            try:
                existing = self.workspace.read_text(slug, path)
            except ValueError:
                pass
            prompt = f"""You are a senior developer editing exactly one UTF-8 file.
Overall request: {request}
Project plan: {json.dumps(plan, ensure_ascii=False)}
Target file: {path}
Purpose: {purpose}

Existing target content:
{existing}

Project context:
{context}
Files already prepared during this update:
{generated_context or "(none)"}

Return the COMPLETE final file, without explanations, in this exact wrapper:
<<<CONTENT>>>
complete content
<<<END>>>
Never use placeholders or omit unchanged sections.
"""
            content = extract_content(self.ollama.generate(model, prompt, num_predict=8192, timeout=300))
            generated.append((path, content))
            generated_context += f"\n--- PREPARED FILE: {path} ---\n{content}\n"

        writes = [asdict(self.workspace.write_text(slug, path, content)) for path, content in generated]
        issues = validate_project(self.workspace, slug)
        return {"summary": str(plan.get("summary", "Done")), "files": writes, "issues": issues}
