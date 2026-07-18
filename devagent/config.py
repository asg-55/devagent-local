from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    projects_root: Path
    state_root: Path
    database_path: Path
    ollama_url: str
    default_model: str
    complex_model: str = "qwen2.5-coder:32b"
    max_file_bytes: int = 1_000_000
    max_context_chars: int = 60_000
    max_agent_steps: int = 32

    @classmethod
    def from_env(cls) -> "Settings":
        workspace = Path(os.getenv("DEVAGENT_WORKSPACE", "/workspace")).resolve()
        projects = Path(os.getenv("DEVAGENT_PROJECTS", str(workspace / "projects"))).resolve()
        state = Path(os.getenv("DEVAGENT_STATE", str(workspace / ".devagent"))).resolve()
        return cls(
            workspace_root=workspace,
            projects_root=projects,
            state_root=state,
            database_path=state / "devagent.db",
            ollama_url=os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/"),
            default_model=os.getenv("DEVAGENT_MODEL", "qwen2.5-coder:7b"),
            complex_model=os.getenv("DEVAGENT_COMPLEX_MODEL", "qwen2.5-coder:32b"),
            max_file_bytes=int(os.getenv("DEVAGENT_MAX_FILE_BYTES", "1000000")),
            max_context_chars=int(os.getenv("DEVAGENT_MAX_CONTEXT_CHARS", "60000")),
            max_agent_steps=int(os.getenv("DEVAGENT_MAX_STEPS", "32")),
        )

    def ensure_directories(self) -> None:
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        (self.state_root / "checkpoints").mkdir(parents=True, exist_ok=True)
        (self.state_root / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.state_root / "previews").mkdir(parents=True, exist_ok=True)
