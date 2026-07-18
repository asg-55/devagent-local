from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .agent import AgentService
from .config import Settings
from .ollama import OllamaClient, OllamaError
from .storage import Storage
from .workspace import WorkspaceError, WorkspaceManager


logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    settings.ensure_directories()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    storage = Storage(settings.database_path)
    storage.initialize()
    workspace = WorkspaceManager(settings.projects_root, settings.state_root, settings.max_file_bytes)
    ollama = OllamaClient(settings.ollama_url)
    agent = AgentService(ollama, workspace, max_steps=settings.max_agent_steps)

    app.extensions["devagent"] = {
        "settings": settings,
        "storage": storage,
        "workspace": workspace,
        "ollama": ollama,
        "agent": agent,
    }

    @app.get("/")
    def index():
        return render_template("index.html", default_model=settings.default_model)

    @app.get("/api/health")
    def health():
        try:
            models = ollama.model_names()
            ollama_status = "ok"
        except OllamaError as exc:
            models = []
            ollama_status = str(exc)
        return jsonify({"status": "ok", "ollama": ollama_status, "models": models})

    @app.get("/api/models")
    def models():
        return jsonify({"models": ollama.models(), "default": settings.default_model})

    @app.get("/api/projects")
    def list_projects():
        return jsonify({"projects": storage.list_projects()})

    @app.post("/api/projects")
    def create_project():
        body = request.get_json(silent=True) or {}
        slug = workspace.validate_slug(str(body.get("slug", "")))
        title = str(body.get("title", "")).strip() or slug
        if any(item["slug"] == slug for item in storage.list_projects()):
            return jsonify({"error": "A project with this name already exists"}), 409
        workspace.create_project(slug)
        project = storage.create_project(slug, title[:120])
        session = storage.create_session(project["id"], "Новая задача", settings.default_model)
        return jsonify({"project": project, "session": session}), 201

    @app.get("/api/projects/<project_id>/sessions")
    def list_sessions(project_id: str):
        storage.get_project(project_id)
        return jsonify({"sessions": storage.list_sessions(project_id)})

    @app.post("/api/projects/<project_id>/sessions")
    def create_session(project_id: str):
        body = request.get_json(silent=True) or {}
        title = str(body.get("title", "")).strip() or "Новая задача"
        model = str(body.get("model", "")).strip() or settings.default_model
        session = storage.create_session(project_id, title[:120], model)
        return jsonify({"session": session}), 201

    @app.get("/api/sessions/<session_id>/messages")
    def messages(session_id: str):
        storage.get_session(session_id)
        return jsonify({"messages": storage.list_messages(session_id)})

    @app.get("/api/projects/<project_id>/files")
    def files(project_id: str):
        project = storage.get_project(project_id)
        return jsonify({"files": workspace.list_files(project["slug"])})

    @app.get("/api/projects/<project_id>/file/<path:relative_path>")
    def file_content(project_id: str, relative_path: str):
        project = storage.get_project(project_id)
        return jsonify({"path": relative_path, "content": workspace.read_text(project["slug"], relative_path)})

    @app.get("/preview/<project_id>/<path:relative_path>")
    def preview(project_id: str, relative_path: str):
        project = storage.get_project(project_id)
        built_root = settings.state_root / "previews" / project["slug"]
        built_path = (built_root / relative_path).resolve(strict=False)
        if (workspace.project_root(project["slug"]) / "package.json").is_file() and built_root.is_dir():
            try:
                built_path.relative_to(built_root.resolve())
            except ValueError:
                raise WorkspaceError("Invalid preview path")
            if built_path.is_file():
                return send_from_directory(built_root, relative_path)
        path = workspace.resolve(project["slug"], relative_path, must_exist=True)
        root = workspace.project_root(project["slug"])
        return send_from_directory(root, path.relative_to(root).as_posix())

    @app.get("/artifacts/<path:relative_path>")
    def artifact(relative_path: str):
        return send_from_directory(settings.state_root / "artifacts", relative_path)

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        session_id = str(body.get("session_id", "")).strip()
        message = str(body.get("message", "")).strip()
        model = str(body.get("model", "")).strip() or settings.default_model
        if not session_id or not message:
            return jsonify({"error": "session_id and message are required"}), 400

        session = storage.get_session(session_id)
        project = storage.get_project(session["project_id"])
        installed_models = ollama.model_names()
        if model not in installed_models:
            return jsonify({"error": "The selected model is not installed in Ollama"}), 400

        storage.add_message(session_id, "user", message)
        history = storage.list_messages(session_id, limit=20)
        quality_profile = AgentService._quality_profile(history)
        execution_model = (
            settings.complex_model
            if quality_profile == "polished" and settings.complex_model in installed_models
            else model
        )
        result = agent.run(project["slug"], history, execution_model)
        assistant_text = result.message
        metadata = {
            "files": result.files,
            "issues": result.issues,
            "steps": result.steps,
            "screenshots": result.screenshots,
            "model_used": execution_model,
            "quality_profile": quality_profile,
        }
        storage.add_message(session_id, "assistant", assistant_text, metadata)
        return jsonify({"message": assistant_text, **metadata})

    @app.errorhandler(KeyError)
    def not_found(exc: KeyError):
        return jsonify({"error": str(exc.args[0] if exc.args else exc)}), 404

    @app.errorhandler(WorkspaceError)
    def invalid_workspace_request(exc: WorkspaceError):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(OllamaError)
    def ollama_failure(exc: OllamaError):
        return jsonify({"error": str(exc)}), 503

    @app.errorhandler(ValueError)
    def invalid_request(exc: ValueError):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(Exception)
    def unexpected_error(exc: Exception):
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description}), exc.code
        logger.exception("Unhandled request failure")
        return jsonify({"error": "Internal error. See the agent log for details."}), 500

    return app
