import tempfile
import unittest
from pathlib import Path

from devagent.agent import AgentService
from devagent.workspace import WorkspaceManager


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, model, messages, tools, num_predict=4096, timeout=300, num_ctx=8192):
        self.calls.append({"model": model, "messages": messages, "tools": tools, "timeout": timeout, "num_ctx": num_ctx})
        return self.responses.pop(0)


class FakeBrowserChecker:
    def run(self, slug, quality_profile="standard"):
        return {
            "passed": True, "applicable": True, "skipped": False, "issues": [],
            "viewports": [{"name": "desktop"}, {"name": "mobile"}],
            "screenshots": [f"{slug}/run/desktop.png", f"{slug}/run/mobile.png"],
        }


class AgentServiceTests(unittest.TestCase):
    def test_parses_multiple_fallback_calls(self):
        message = {"content": '{"name":"list_files","arguments":{}}\n\n{"name":"read_file","arguments":{"path":"a.txt"}}'}
        calls = AgentService._tool_calls(message)
        self.assertEqual([call["name"] for call in calls], ["list_files", "read_file"])

    def test_detects_polished_quality_request(self):
        history = [{"role": "user", "content": "Сделай супер современный премиальный дизайн"}]
        self.assertEqual(AgentService._quality_profile(history), "polished")

    def test_hybrid_tool_loop_writes_valid_project(self):
        responses = [
            {"role": "assistant", "content": '{"name":"list_files","arguments":{}}'},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {
                "name": "write_file", "arguments": {"path": "index.html", "content": "<!doctype html><html><body>OK</body></html>"}
            }}]},
            {"role": "assistant", "content": '{"name":"run_project_checks","arguments":{}}'},
            {"role": "assistant", "content": '{"name":"browser_check","arguments":{}}'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects").mkdir()
            (root / "state").mkdir()
            workspace = WorkspaceManager(root / "projects", root / "state")
            workspace.create_project("demo")
            agent = AgentService(FakeOllama(responses), workspace, max_steps=8, browser_checker=FakeBrowserChecker())

            result = agent.run("demo", [{"role": "user", "content": "Создай страницу"}], "test-model")
            self.assertIn("браузерные проверки пройдены", result.message)
            self.assertEqual([step["tool"] for step in result.steps], ["list_files", "write_file", "run_project_checks", "browser_check"])
            self.assertEqual(result.issues, [])
            self.assertEqual(result.files[0]["path"], "index.html")

    def test_does_not_finish_immediately_after_tool_error(self):
        responses = [
            {"role": "assistant", "content": '{"name":"run_project_checks","arguments":{}}'},
            {"role": "assistant", "content": "Please provide a path."},
            {"role": "assistant", "content": '{"name":"list_files","arguments":{}}'},
            {"role": "assistant", "content": '{"name":"read_file","arguments":{"path":"index.html"}}'},
            {"role": "assistant", "content": '{"name":"run_project_checks","arguments":{}}'},
            {"role": "assistant", "content": '{"name":"browser_check","arguments":{}}'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects").mkdir()
            (root / "state").mkdir()
            workspace = WorkspaceManager(root / "projects", root / "state")
            workspace.create_project("demo")
            workspace.write_text("demo", "index.html", "<!doctype html><html><body></body></html>")
            agent = AgentService(FakeOllama(responses), workspace, max_steps=10, browser_checker=FakeBrowserChecker())
            result = agent.run("demo", [{"role": "user", "content": "Проверь проект"}], "test-model")
            self.assertEqual(result.message, "Проверка завершена: статических и браузерных ошибок не найдено.")
            self.assertEqual(result.steps[-1]["tool"], "browser_check")

    def test_empty_project_recovers_from_guessed_missing_file(self):
        responses = [
            {"role": "assistant", "content": '{"name":"list_files","arguments":{}}'},
            {"role": "assistant", "content": '{"name":"read_file","arguments":{"path":"src/App.jsx"}}'},
            {"role": "assistant", "content": '{"name":"write_file","arguments":{"path":"package.json","content":"{\\"dependencies\\":{\\"react\\":\\"19.2.7\\",\\"react-dom\\":\\"19.2.7\\"},\\"devDependencies\\":{\\"vite\\":\\"8.1.4\\"}}"}}'},
            {"role": "assistant", "content": '{"name":"write_file","arguments":{"path":"index.html","content":"<!doctype html><html><body><div id=\\"root\\"></div><script type=\\"module\\" src=\\"/src/main.jsx\\"></script></body></html>"}}'},
            {"role": "assistant", "content": '{"name":"write_file","arguments":{"path":"src/main.jsx","content":"document.getElementById(\\"root\\").textContent=\\"Working React project\\";"}}'},
            {"role": "assistant", "content": '{"name":"run_project_checks","arguments":{}}'},
            {"role": "assistant", "content": '{"name":"browser_check","arguments":{}}'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects").mkdir()
            (root / "state").mkdir()
            workspace = WorkspaceManager(root / "projects", root / "state")
            workspace.create_project("demo")
            agent = AgentService(FakeOllama(responses), workspace, max_steps=12, browser_checker=FakeBrowserChecker())
            result = agent.run("demo", [{"role": "user", "content": "Создай React сайт"}], "test-model")
            self.assertIn("браузерные проверки пройдены", result.message)
            self.assertTrue((root / "projects" / "demo" / "package.json").is_file())
            self.assertEqual(result.steps[1]["status"], "error")


if __name__ == "__main__":
    unittest.main()
