import shutil
import tempfile
import unittest
from pathlib import Path

from devagent.browser_check import BrowserChecker
from devagent.workspace import WorkspaceManager


@unittest.skipUnless(shutil.which("chromium") and shutil.which("chromedriver"), "Chromium is not installed")
class BrowserCheckerTests(unittest.TestCase):
    def make_workspace(self, root: Path) -> WorkspaceManager:
        (root / "projects").mkdir()
        (root / "state").mkdir()
        workspace = WorkspaceManager(root / "projects", root / "state")
        workspace.create_project("demo")
        return workspace

    def test_valid_page_renders_at_desktop_and_mobile_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.make_workspace(root)
            workspace.write_text("demo", "index.html", """<!doctype html><html><head><link rel=\"stylesheet\" href=\"style.css\"></head><body><main><h1>Hello browser</h1><p>Visible content works.</p></main><script src=\"app.js\"></script></body></html>""")
            workspace.write_text("demo", "style.css", "body{margin:0;max-width:100%;background:#123;color:white}")
            workspace.write_text("demo", "app.js", "document.body.dataset.ready='yes';")
            result = BrowserChecker(workspace, root / "state" / "artifacts").run("demo")
            self.assertTrue(result["passed"], result["issues"])
            self.assertEqual([item["name"] for item in result["viewports"]], ["desktop", "mobile"])
            self.assertEqual(len(result["screenshots"]), 2)
            for relative in result["screenshots"]:
                self.assertTrue((root / "state" / "artifacts" / relative).is_file())

    def test_reports_javascript_errors_and_horizontal_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.make_workspace(root)
            workspace.write_text("demo", "index.html", """<!doctype html><html><body><div style=\"width:1200px\">Long visible page content</div><script>throw new Error('boom')</script></body></html>""")
            result = BrowserChecker(workspace, root / "state" / "artifacts").run("demo")
            messages = " ".join(item["message"] for item in result["issues"])
            self.assertFalse(result["passed"])
            self.assertIn("horizontal overflow", messages)
            self.assertIn("boom", messages)

    def test_renders_standard_react_vite_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.make_workspace(root)
            workspace.write_text("demo", "package.json", '{"scripts":{"dev":"malicious-command-is-never-run"},"dependencies":{"react":"19.2.7","react-dom":"19.2.7"},"devDependencies":{"vite":"8.1.4"}}')
            workspace.write_text("demo", "index.html", '<!doctype html><html><head><title>React test</title></head><body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>')
            workspace.write_text("demo", "src/main.jsx", "import React from 'react'; import {createRoot} from 'react-dom/client'; import './style.css'; createRoot(document.getElementById('root')).render(<main><h1>Managed React</h1><button>Working button</button></main>);")
            workspace.write_text("demo", "src/style.css", "body{margin:0;background:#123;color:white} main{padding:2rem}")
            result = BrowserChecker(workspace, root / "state" / "artifacts").run("demo")
            self.assertTrue(result["passed"], result["issues"])
            self.assertEqual(result["runtime"], "vite")
            self.assertGreater(result["viewports"][0]["bodyTextLength"], 10)
            self.assertTrue((root / "state" / "previews" / "demo" / "index.html").is_file())

    def test_rejects_unsupported_vite_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.make_workspace(root)
            workspace.write_text("demo", "package.json", '{"dependencies":{"unknown-package":"1.0.0"}}')
            workspace.write_text("demo", "index.html", "<!doctype html><html><body>Unsupported dependency</body></html>")
            result = BrowserChecker(workspace, root / "state" / "artifacts").run("demo")
            self.assertFalse(result["passed"])
            self.assertIn("Unsupported packages", result["issues"][0]["message"])

    def test_reports_tabs_that_do_not_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self.make_workspace(root)
            workspace.write_text("demo", "index.html", """<!doctype html><html><body><div role=\"tablist\"><button role=\"tab\" aria-selected=\"true\">One</button><button role=\"tab\" aria-selected=\"false\">Two</button></div><section role=\"tabpanel\">Static panel content</section></body></html>""")
            result = BrowserChecker(workspace, root / "state" / "artifacts").run("demo")
            messages = " ".join(item["message"] for item in result["issues"])
            self.assertFalse(result["passed"])
            self.assertIn("do not react to clicks", messages)


if __name__ == "__main__":
    unittest.main()
