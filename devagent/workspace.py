from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
IGNORED_PARTS = {".git", ".devagent", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
TEXT_EXTENSIONS = {
    ".html", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt",
    ".py", ".toml", ".yaml", ".yml", ".xml", ".svg", ".sql", ".sh", ".bat", ".ps1", ".env",
}


class WorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class WriteResult:
    path: str
    bytes_written: int
    checkpoint: str | None


@dataclass(frozen=True)
class DeleteResult:
    path: str
    checkpoint: str


class WorkspaceManager:
    def __init__(self, projects_root: Path, state_root: Path, max_file_bytes: int = 1_000_000):
        self.projects_root = projects_root.resolve()
        self.checkpoints_root = (state_root / "checkpoints").resolve()
        self.max_file_bytes = max_file_bytes

    @staticmethod
    def validate_slug(slug: str) -> str:
        normalized = slug.strip().lower()
        if not SLUG_PATTERN.fullmatch(normalized):
            raise WorkspaceError("Use 1-63 lowercase Latin letters, digits, '-' or '_'")
        return normalized

    def create_project(self, slug: str) -> Path:
        slug = self.validate_slug(slug)
        root = (self.projects_root / slug).resolve()
        if root.parent != self.projects_root:
            raise WorkspaceError("Invalid project path")
        root.mkdir(parents=True, exist_ok=False)
        return root

    def project_root(self, slug: str) -> Path:
        slug = self.validate_slug(slug)
        root = (self.projects_root / slug).resolve()
        if root.parent != self.projects_root or not root.is_dir():
            raise WorkspaceError("Project directory not found")
        return root

    def resolve(self, slug: str, relative_path: str, must_exist: bool = False) -> Path:
        root = self.project_root(slug)
        raw = relative_path.strip().replace("\\", "/")
        if not raw or raw.startswith("/") or "\x00" in raw:
            raise WorkspaceError("Invalid relative path")
        candidate = (root / raw).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Path escapes the project directory") from exc
        if must_exist and not candidate.exists():
            raise WorkspaceError("File not found")
        return candidate

    def list_files(self, slug: str) -> list[dict[str, int | str]]:
        root = self.project_root(slug)
        files: list[dict[str, int | str]] = []
        for path in sorted(root.rglob("*")):
            if any(part in IGNORED_PARTS for part in path.relative_to(root).parts):
                continue
            if path.is_file():
                files.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size})
        return files

    def read_text(self, slug: str, relative_path: str) -> str:
        path = self.resolve(slug, relative_path, must_exist=True)
        if not path.is_file():
            raise WorkspaceError("Path is not a file")
        if path.stat().st_size > self.max_file_bytes:
            raise WorkspaceError("File is too large")
        if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {"Dockerfile", "Makefile"}:
            raise WorkspaceError("Binary or unsupported file type")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("File is not valid UTF-8 text") from exc

    def write_text(self, slug: str, relative_path: str, content: str) -> WriteResult:
        requested = Path(relative_path.replace("\\", "/"))
        if requested.suffix.lower() not in TEXT_EXTENSIONS and requested.name not in {"Dockerfile", "Makefile", ".env"}:
            raise WorkspaceError("Only UTF-8 text files can be written; use an external URL or SVG for images")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise WorkspaceError("Generated file exceeds the size limit")
        path = self.resolve(slug, relative_path)
        checkpoint = self._checkpoint(slug, path) if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".devagent-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return WriteResult(path=relative_path.replace("\\", "/"), bytes_written=len(encoded), checkpoint=checkpoint)

    def replace_text(self, slug: str, relative_path: str, old: str, new: str) -> WriteResult:
        if not old:
            raise WorkspaceError("Replacement target cannot be empty")
        content = self.read_text(slug, relative_path)
        occurrences = content.count(old)
        if occurrences != 1:
            raise WorkspaceError(f"Expected exactly one match, found {occurrences}")
        return self.write_text(slug, relative_path, content.replace(old, new, 1))

    def delete_file(self, slug: str, relative_path: str) -> DeleteResult:
        path = self.resolve(slug, relative_path, must_exist=True)
        if not path.is_file():
            raise WorkspaceError("Only files can be deleted")
        checkpoint = self._checkpoint(slug, path)
        path.unlink()
        return DeleteResult(path=relative_path.replace("\\", "/"), checkpoint=checkpoint)

    def project_context(self, slug: str, max_chars: int) -> str:
        chunks: list[str] = []
        used = 0
        for item in self.list_files(slug):
            path = str(item["path"])
            try:
                content = self.read_text(slug, path)
            except WorkspaceError:
                continue
            block = f"\n--- FILE: {path} ---\n{content}\n"
            if used + len(block) > max_chars:
                break
            chunks.append(block)
            used += len(block)
        return "".join(chunks) or "(project has no files yet)"

    def _checkpoint(self, slug: str, source: Path) -> str:
        root = self.project_root(slug)
        relative = source.relative_to(root)
        checkpoint_id = uuid.uuid4().hex
        destination = self.checkpoints_root / checkpoint_id / slug / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return checkpoint_id
