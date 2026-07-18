import tempfile
import unittest
from pathlib import Path

from devagent.storage import Storage


class StorageTests(unittest.TestCase):
    def test_project_session_and_messages_survive_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "agent.db"
            storage = Storage(database)
            storage.initialize()
            project = storage.create_project("demo", "Demo")
            session = storage.create_session(project["id"], "Task", "model")
            storage.add_message(session["id"], "user", "hello")
            storage.add_message(session["id"], "assistant", "done", {"files": ["index.html"]})

            reopened = Storage(database)
            reopened.initialize()
            loaded = reopened.list_messages(session["id"])
            self.assertEqual([item["content"] for item in loaded], ["hello", "done"])
            self.assertEqual(loaded[1]["metadata"]["files"], ["index.html"])


if __name__ == "__main__":
    unittest.main()

