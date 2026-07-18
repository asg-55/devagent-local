import tempfile
import unittest
from pathlib import Path

from devagent.workspace import WorkspaceError, WorkspaceManager


class WorkspaceManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.projects = root / "projects"
        self.state = root / "state"
        self.projects.mkdir()
        self.state.mkdir()
        self.workspace = WorkspaceManager(self.projects, self.state, max_file_bytes=1000)
        self.workspace.create_project("demo")

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_parent_traversal(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve("demo", "folder/../../../secret.txt")

    def test_rejects_absolute_path(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve("demo", "/etc/passwd")

    def test_write_read_and_checkpoint(self):
        first = self.workspace.write_text("demo", "index.html", "first")
        second = self.workspace.write_text("demo", "index.html", "second")
        self.assertIsNone(first.checkpoint)
        self.assertIsNotNone(second.checkpoint)
        self.assertEqual(self.workspace.read_text("demo", "index.html"), "second")

    def test_replace_requires_one_exact_match(self):
        self.workspace.write_text("demo", "app.js", "same same")
        with self.assertRaises(WorkspaceError):
            self.workspace.replace_text("demo", "app.js", "same", "new")

    def test_rejects_large_files(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.write_text("demo", "large.txt", "x" * 1001)

    def test_rejects_fake_binary_file(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.write_text("demo", "image.jpg", "not an image")

    def test_delete_creates_checkpoint(self):
        self.workspace.write_text("demo", "old.txt", "recoverable")
        result = self.workspace.delete_file("demo", "old.txt")
        self.assertFalse((self.projects / "demo" / "old.txt").exists())
        self.assertTrue(result.checkpoint)


if __name__ == "__main__":
    unittest.main()
