import tempfile
import unittest
from pathlib import Path

from devagent.tools import ToolExecutor
from devagent.workspace import WorkspaceManager


class ToolExecutorTests(unittest.TestCase):
    def test_reads_searches_replaces_and_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects").mkdir()
            (root / "state").mkdir()
            workspace = WorkspaceManager(root / "projects", root / "state")
            workspace.create_project("demo")
            tools = ToolExecutor(workspace, "demo")

            tools.execute("write_file", {"path": "app.js", "content": "const answer = 41;"})
            self.assertEqual(len(tools.execute("list_files", {})["files"]), 1)
            self.assertEqual(len(tools.execute("search_text", {"query": "answer"})["matches"]), 1)
            read = tools.execute("read_file", {"path": "app.js"})
            self.assertIn("const answer", read["content"])
            tools.execute("replace_text", {"path": "app.js", "old": "41", "new": "42"})
            self.assertIn("42", workspace.read_text("demo", "app.js"))
            self.assertTrue(tools.execute("run_project_checks", {})["check_result"]["passed"])
            tools.execute("delete_file", {"path": "app.js"})
            self.assertEqual(tools.execute("list_files", {})["files"], [])

    def test_removes_repeated_json_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects").mkdir()
            (root / "state").mkdir()
            workspace = WorkspaceManager(root / "projects", root / "state")
            workspace.create_project("demo")
            tools = ToolExecutor(workspace, "demo")
            tools.execute("write_file", {"path": "data.json", "content": '{"items":[{"image":"a"},{"image":"b"}]}'})
            result = tools.execute("remove_json_key", {"path": "data.json", "key": "image"})
            self.assertEqual(result["removed"], 2)
            self.assertNotIn("image", workspace.read_text("demo", "data.json"))


if __name__ == "__main__":
    unittest.main()
