import tempfile
import unittest
from pathlib import Path

from devagent.validation import validate_project
from devagent.workspace import WorkspaceManager


class ProjectValidationTests(unittest.TestCase):
    def test_reports_missing_local_assets_and_markdown_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            state = root / "state"
            projects.mkdir()
            state.mkdir()
            workspace = WorkspaceManager(projects, state)
            workspace.create_project("demo")
            workspace.write_text(
                "demo",
                "index.html",
                '```html\n<!doctype html><html><head><link href="style.css" rel="stylesheet"></head>'
                '<body><img src="image.png"></body></html>',
            )

            issues = validate_project(workspace, "demo")
            codes = [issue["code"] for issue in issues]
            self.assertIn("markdown_fence", codes)
            self.assertEqual(codes.count("missing_reference"), 2)

    def test_accepts_present_and_remote_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            state = root / "state"
            projects.mkdir()
            state.mkdir()
            workspace = WorkspaceManager(projects, state)
            workspace.create_project("demo")
            workspace.write_text("demo", "style.css", "body {}")
            workspace.write_text(
                "demo", "index.html",
                '<!doctype html><html><head><link href="style.css"><script src="https://cdn.example/app.js"></script>'
                '</head><body></body></html>',
            )
            self.assertEqual(validate_project(workspace, "demo"), [])

    def test_reports_stylesheet_not_connected_to_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            state = root / "state"
            projects.mkdir()
            state.mkdir()
            workspace = WorkspaceManager(projects, state)
            workspace.create_project("demo")
            workspace.write_text("demo", "style.css", "body {}")
            workspace.write_text("demo", "index.html", "<!doctype html><html><body></body></html>")
            codes = [issue["code"] for issue in validate_project(workspace, "demo")]
            self.assertIn("unreferenced_asset", codes)

    def test_accepts_css_imported_by_managed_vite_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            state = root / "state"
            projects.mkdir()
            state.mkdir()
            workspace = WorkspaceManager(projects, state)
            workspace.create_project("demo")
            workspace.write_text("demo", "package.json", '{"devDependencies":{"vite":"8.1.4"}}')
            workspace.write_text("demo", "index.html", '<!doctype html><html><body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>')
            workspace.write_text("demo", "src/main.jsx", "import './style.css';")
            workspace.write_text("demo", "src/style.css", "body {}")
            self.assertEqual(validate_project(workspace, "demo"), [])

    def test_reports_literal_newline_text_in_jsx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects").mkdir()
            (root / "state").mkdir()
            workspace = WorkspaceManager(root / "projects", root / "state")
            workspace.create_project("demo")
            workspace.write_text("demo", "src/App.jsx", "const App=()=> <form><label>Name</label>\\n<input /></form>;")
            codes = [issue["code"] for issue in validate_project(workspace, "demo")]
            self.assertIn("literal_newline_escape", codes)


if __name__ == "__main__":
    unittest.main()
