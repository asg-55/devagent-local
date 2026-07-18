import tempfile
import unittest
from pathlib import Path

from devagent.config import Settings
from devagent.web import create_app


class WebApiTests(unittest.TestCase):
    def test_creates_project_and_initial_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                workspace_root=root,
                projects_root=root / "projects",
                state_root=root / "state",
                database_path=root / "state" / "agent.db",
                ollama_url="http://127.0.0.1:1",
                default_model="test-model",
            )
            app = create_app(settings)
            app.testing = True
            client = app.test_client()

            response = client.post("/api/projects", json={"slug": "coffee_shop", "title": "Кофейня"})
            self.assertEqual(response.status_code, 201)
            payload = response.get_json()
            self.assertEqual(payload["project"]["slug"], "coffee_shop")
            self.assertEqual(payload["project"]["title"], "Кофейня")
            self.assertTrue((root / "projects" / "coffee_shop").is_dir())

            projects = client.get("/api/projects").get_json()["projects"]
            sessions = client.get(f'/api/projects/{payload["project"]["id"]}/sessions').get_json()["sessions"]
            self.assertEqual(len(projects), 1)
            self.assertEqual(len(sessions), 1)

    def test_rejects_traversal_project_slug(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                workspace_root=root,
                projects_root=root / "projects",
                state_root=root / "state",
                database_path=root / "state" / "agent.db",
                ollama_url="http://127.0.0.1:1",
                default_model="test-model",
            )
            app = create_app(settings)
            app.testing = True
            response = app.test_client().post("/api/projects", json={"slug": "../escape", "title": "Bad"})
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

