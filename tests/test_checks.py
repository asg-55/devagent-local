import tempfile
import unittest
from pathlib import Path

from devagent.checks import ProjectChecker, SafeCommandRunner
from devagent.workspace import WorkspaceError, WorkspaceManager


class ProjectCheckerTests(unittest.TestCase):
    def make_workspace(self, root: Path) -> WorkspaceManager:
        (root / "projects").mkdir()
        (root / "state").mkdir()
        workspace = WorkspaceManager(root / "projects", root / "state")
        workspace.create_project("demo")
        return workspace

    def test_reports_python_and_css_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            workspace.write_text("demo", "broken.py", "def broken(:\n    pass")
            workspace.write_text("demo", "broken.css", "body { background: red, }")
            result = ProjectChecker(workspace).run("demo")
            codes = {issue["code"] for issue in result["issues"]}
            self.assertIn("python_syntax", codes)
            self.assertIn("css_trailing_comma", codes)
            self.assertFalse(result["passed"])

    def test_safe_runner_rejects_arbitrary_command(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(WorkspaceError):
                SafeCommandRunner().run(["sh", "-c", "echo unsafe"], Path(directory))


if __name__ == "__main__":
    unittest.main()
