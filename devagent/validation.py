from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .workspace import WorkspaceError, WorkspaceManager


IGNORED_SCHEMES = {"http", "https", "data", "mailto", "tel", "javascript"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
LITERAL_MARKUP_NEWLINE = re.compile(r">\\n\s*<")


class ReferenceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "img", "source", "video", "audio", "iframe"} and attributes.get("src"):
            self.references.append(str(attributes["src"]))
        if tag == "link" and attributes.get("href"):
            self.references.append(str(attributes["href"]))


def validate_project(workspace: WorkspaceManager, slug: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    paths = {str(item["path"]) for item in workspace.list_files(slug)}
    html_references: set[str] = set()
    html_files = sorted(path for path in paths if path.lower().endswith(".html"))

    for html_path in html_files:
        content = workspace.read_text(slug, html_path)
        if content.lstrip().startswith("```"):
            issues.append({"file": html_path, "code": "markdown_fence", "message": "HTML starts with a Markdown fence"})
        if "</html>" not in content.lower():
            issues.append({"file": html_path, "code": "incomplete_html", "message": "Missing closing </html> tag"})
        parser = ReferenceParser()
        try:
            parser.feed(content)
        except Exception as exc:
            issues.append({"file": html_path, "code": "invalid_html", "message": f"HTML parser error: {exc}"})
            continue
        parent = PurePosixPath(html_path).parent
        for reference in parser.references:
            _check_reference(workspace, slug, paths, html_path, parent, reference, issues, html_references)

    for source_path in sorted(path for path in paths if PurePosixPath(path).suffix.lower() in {".html", ".jsx", ".tsx"}):
        if LITERAL_MARKUP_NEWLINE.search(workspace.read_text(slug, source_path)):
            issues.append({
                "file": source_path,
                "code": "literal_newline_escape",
                "message": "Markup contains literal \\n text between elements; replace it with actual line breaks",
            })

    if html_files and "package.json" not in paths:
        for source_path in sorted(path for path in paths if PurePosixPath(path).suffix.lower() in {".css", ".js", ".mjs"}):
            if source_path not in html_references:
                issues.append({
                    "file": source_path,
                    "code": "unreferenced_asset",
                    "message": "CSS or JavaScript file exists but is not referenced by any HTML page",
                })

    for css_path in sorted(path for path in paths if path.lower().endswith(".css")):
        content = workspace.read_text(slug, css_path)
        parent = PurePosixPath(css_path).parent
        for match in CSS_URL.finditer(content):
            _check_reference(workspace, slug, paths, css_path, parent, match.group(2), issues)

    for json_path in sorted(path for path in paths if path.lower().endswith(".json")):
        try:
            data = json.loads(workspace.read_text(slug, json_path))
        except json.JSONDecodeError as exc:
            issues.append({"file": json_path, "code": "invalid_json", "message": f"Invalid JSON: {exc.msg}"})
            continue
        parent = PurePosixPath(json_path).parent
        for reference in _json_asset_references(data):
            _check_reference(workspace, slug, paths, json_path, parent, reference, issues)

    for asset_path in sorted(path for path in paths if PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS):
        path = workspace.resolve(slug, asset_path, must_exist=True)
        header = path.read_bytes()[:16]
        suffix = path.suffix.lower()
        valid = (
            (suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
            or (suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"))
            or (suffix == ".gif" and header.startswith((b"GIF87a", b"GIF89a")))
            or (suffix == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP")
        )
        if not valid:
            issues.append({
                "file": asset_path,
                "code": "invalid_binary",
                "message": "File extension indicates an image, but content is not a valid image",
            })
    return issues


def _check_reference(
    workspace: WorkspaceManager,
    slug: str,
    paths: set[str],
    source_path: str,
    parent: PurePosixPath,
    raw_reference: str,
    issues: list[dict[str, str]],
    referenced_paths: set[str] | None = None,
) -> None:
    parsed = urlsplit(raw_reference)
    if parsed.scheme.lower() in IGNORED_SCHEMES or parsed.netloc or not parsed.path:
        return
    decoded = unquote(parsed.path).replace("\\", "/")
    relative = decoded.lstrip("/") if decoded.startswith("/") else (parent / decoded).as_posix()
    try:
        resolved = workspace.resolve(slug, relative)
        root = workspace.project_root(slug)
        normalized = resolved.relative_to(root).as_posix()
    except WorkspaceError:
        issues.append({"file": source_path, "code": "unsafe_reference", "message": f"Unsafe local reference: {raw_reference}"})
        return
    if normalized not in paths:
        issues.append({"file": source_path, "code": "missing_reference", "message": f"Missing local file: {raw_reference}"})
    elif referenced_paths is not None:
        referenced_paths.add(normalized)


def _json_asset_references(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _json_asset_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_asset_references(child)
    elif isinstance(value, str):
        path = urlsplit(value).path
        if PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS:
            yield value
