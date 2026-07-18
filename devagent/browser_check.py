from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .workspace import WorkspaceManager


VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
SUPPORTED_WEB_PACKAGES = {"vite", "react", "react-dom", "@types/react", "@types/react-dom"}
VITE_CONFIG_NAMES = {
    "vite.config.js", "vite.config.mjs", "vite.config.cjs", "vite.config.ts", "vite.config.mts", "vite.config.cts"
}
RUNTIME_NODE_MODULES = Path("/opt/devagent-web-runtime/node_modules")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@dataclass
class _PreviewHandle:
    url: str
    runtime: str
    close: Callable[[], None]
    publish: Callable[[], None] | None = None


class BrowserChecker:
    """Render static or standard React/Vite sites and report visible runtime failures."""

    def __init__(self, workspace: WorkspaceManager, artifacts_root: Path):
        self.workspace = workspace
        self.artifacts_root = artifacts_root.resolve()

    def run(self, slug: str, quality_profile: str = "standard") -> dict[str, Any]:
        root = self.workspace.project_root(slug)
        if not (root / "index.html").is_file():
            return self._skipped()
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
        except ImportError as exc:
            return self._failure(f"Browser check unavailable: {exc}")

        try:
            preview = self._start_preview(root)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            return self._failure(str(exc))

        run_id = uuid.uuid4().hex
        output_dir = self.artifacts_root / slug / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        options = webdriver.ChromeOptions()
        options.binary_location = "/usr/bin/chromium"
        for argument in (
            "--headless", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--hide-scrollbars",
            "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE 127.0.0.1",
        ):
            options.add_argument(argument)
        options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

        driver = None
        results: list[dict[str, Any]] = []
        screenshots: list[str] = []
        issues: list[dict[str, str]] = []
        try:
            driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
            driver.set_page_load_timeout(20)
            for name, (width, height) in VIEWPORTS.items():
                driver.set_window_size(width, height)
                driver.get(preview.url)
                time.sleep(0.5)
                metrics = driver.execute_script(self._metrics_script())
                severe = [
                    entry for entry in driver.get_log("browser")
                    if entry.get("level") == "SEVERE" and "/favicon.ico" not in entry.get("message", "")
                ]
                screenshot_path = output_dir / f"{name}.png"
                page_height = min(max(height, int(metrics["scrollHeight"])), 4000)
                driver.set_window_size(width, page_height)
                driver.save_screenshot(str(screenshot_path))
                artifact = screenshot_path.relative_to(self.artifacts_root).as_posix()
                screenshots.append(artifact)
                results.append({
                    "name": name, "width": width, "height": height, **metrics,
                    "consoleErrors": [entry.get("message", "")[:1000] for entry in severe],
                    "screenshot": artifact,
                })
                if int(metrics["bodyTextLength"]) < 10:
                    issues.append(self._issue(name, "Page has almost no visible text"))
                if int(metrics["visibleElementCount"]) < 2:
                    issues.append(self._issue(name, "Page has almost no visible elements"))
                if int(metrics["scrollWidth"]) > int(metrics["innerWidth"]) + 2:
                    issues.append(self._issue(name, "Page has horizontal overflow"))
                for entry in severe:
                    issues.append(self._issue(name, f"Browser console: {entry.get('message', '')[:500]}"))
                if name == "desktop":
                    if int(metrics["unlabeledControlCount"]):
                        issues.append(self._issue(name, f"Form controls without an accessible label: {metrics['unlabeledControlCount']}"))
                    if int(metrics["unnamedInteractiveCount"]):
                        issues.append(self._issue(name, f"Buttons or links without an accessible name: {metrics['unnamedInteractiveCount']}"))
                    if int(metrics["formsWithoutSubmitCount"]):
                        issues.append(self._issue(name, f"Forms without a submit control: {metrics['formsWithoutSubmitCount']}"))
                    issues.extend(self._check_tabs(driver))
                    if quality_profile == "polished":
                        if int(metrics["bodyTextLength"]) < 300:
                            issues.append(self._issue(name, "Polished design requested: page content is too sparse"))
                        if int(metrics["sectionCount"]) < 3:
                            issues.append(self._issue(name, "Polished design requested: add at least three meaningful page sections"))
                        if int(metrics["cssRuleCount"]) < 18:
                            issues.append(self._issue(name, "Polished design requested: CSS visual system is too small"))
                        if int(metrics["mediaRuleCount"]) < 1:
                            issues.append(self._issue(name, "Polished design requested: add a responsive CSS media query"))

            project_files = {str(item["path"]) for item in self.workspace.list_files(slug)}
            if any(path.endswith(".css") for path in project_files) and not results[0]["stylesheetCount"]:
                issues.append(self._issue("desktop", "Project CSS exists but no stylesheet was loaded"))
            if any(path.endswith((".js", ".mjs", ".jsx", ".tsx")) for path in project_files) and not results[0]["scriptCount"]:
                issues.append(self._issue("desktop", "Project JavaScript exists but no script was loaded"))
            if not issues and preview.publish is not None:
                preview.publish()
        except Exception as exc:
            issues.append(self._issue("browser", f"Browser check failed: {type(exc).__name__}: {exc}"))
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            preview.close()

        return {
            "passed": not issues, "applicable": True, "skipped": False, "runtime": preview.runtime,
            "issues": issues, "viewports": results, "screenshots": screenshots,
        }

    def _start_preview(self, root: Path) -> _PreviewHandle:
        if (root / "package.json").is_file():
            return self._start_vite(root)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(root)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def close() -> None:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        return _PreviewHandle(f"http://127.0.0.1:{httpd.server_port}/index.html", "static", close)

    def _start_vite(self, root: Path) -> _PreviewHandle:
        if not RUNTIME_NODE_MODULES.is_dir():
            raise ValueError("Managed Vite runtime is not installed")
        forbidden_configs = sorted(path.name for path in root.iterdir() if path.name in VITE_CONFIG_NAMES)
        if forbidden_configs:
            raise ValueError("Custom Vite config is not allowed in managed preview: " + ", ".join(forbidden_configs))
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid package.json: {exc.msg}") from exc
        declared: set[str] = set()
        for section in ("dependencies", "devDependencies"):
            values = package.get(section, {})
            if not isinstance(values, dict):
                raise ValueError(f"package.json {section} must be an object")
            declared.update(str(name) for name in values)
        unsupported = sorted(declared - SUPPORTED_WEB_PACKAGES)
        if unsupported:
            raise ValueError("Unsupported packages in managed preview: " + ", ".join(unsupported))

        temporary = tempfile.TemporaryDirectory(prefix="devagent-vite-")
        project_copy = Path(temporary.name) / "project"
        shutil.copytree(
            root, project_copy,
            ignore=shutil.ignore_patterns("node_modules", ".git", "dist", "build", ".vite", "__pycache__"),
        )
        (project_copy / "node_modules").symlink_to(RUNTIME_NODE_MODULES, target_is_directory=True)
        port = self._free_port()
        command = [
            "node", str(RUNTIME_NODE_MODULES / "vite" / "bin" / "vite.js"), str(project_copy),
            "--host", "127.0.0.1", "--port", str(port), "--strictPort", "--logLevel", "error",
        ]
        process = subprocess.Popen(
            command, cwd=project_copy, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", shell=False,
        )
        url = f"http://127.0.0.1:{port}/"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read()[-2000:] if process.stdout else ""
                if process.stdout:
                    process.stdout.close()
                temporary.cleanup()
                raise ValueError(f"Vite failed to start: {output.strip() or 'unknown error'}")
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    if response.status < 500:
                        break
            except OSError:
                time.sleep(0.15)
        else:
            process.terminate()
            process.wait(timeout=3)
            if process.stdout:
                process.stdout.close()
            temporary.cleanup()
            raise ValueError("Vite did not become ready within 20 seconds")

        def close() -> None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()
            temporary.cleanup()

        def publish() -> None:
            destination = self.artifacts_root.parent / "previews" / root.name
            destination.mkdir(parents=True, exist_ok=True)
            build_command = [
                "node", str(RUNTIME_NODE_MODULES / "vite" / "bin" / "vite.js"), "build", str(project_copy),
                "--outDir", str(destination), "--emptyOutDir", "--base", "./", "--logLevel", "error",
            ]
            result = subprocess.run(
                build_command, cwd=project_copy, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, shell=False, check=False,
            )
            if result.returncode != 0:
                output = (result.stderr or result.stdout or "unknown build error")[-2000:]
                raise ValueError(f"Vite production build failed: {output}")

        return _PreviewHandle(url, "vite", close, publish)

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _metrics_script() -> str:
        return """
        const all = [...document.querySelectorAll('body *')];
        const visible = all.filter((el) => {
          const s = getComputedStyle(el), r = el.getBoundingClientRect();
          return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
        });
        const controls = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select')];
        const unlabeled = controls.filter((el) => !el.labels?.length && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !el.getAttribute('title'));
        const interactive = [...document.querySelectorAll('button, a[href]')];
        const unnamed = interactive.filter((el) => !(el.innerText || '').trim() && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !el.getAttribute('title'));
        const formsWithoutSubmit = [...document.forms].filter((form) => !form.querySelector('button:not([type=button]), input[type=submit]'));
        return {
          title: document.title,
          bodyTextLength: (document.body?.innerText || '').trim().length,
          elementCount: all.length,
          visibleElementCount: visible.length,
          stylesheetCount: document.styleSheets.length,
          scriptCount: document.scripts.length,
          innerWidth: window.innerWidth,
          scrollWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
          scrollHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
          unlabeledControlCount: unlabeled.length,
          unnamedInteractiveCount: unnamed.length,
          formsWithoutSubmitCount: formsWithoutSubmit.length,
          sectionCount: document.querySelectorAll('section').length,
          cssRuleCount: [...document.styleSheets].reduce((total, sheet) => { try { return total + sheet.cssRules.length; } catch { return total; } }, 0),
          mediaRuleCount: [...document.styleSheets].reduce((total, sheet) => { try { return total + [...sheet.cssRules].filter((rule) => rule.constructor.name === 'CSSMediaRule').length; } catch { return total; } }, 0),
        };
        """

    @classmethod
    def _check_tabs(cls, driver: Any) -> list[dict[str, str]]:
        before = driver.execute_script(
            """
            const tabs = [...document.querySelectorAll('[role=tab]')];
            const panels = [...document.querySelectorAll('[role=tabpanel]')];
            return {
              tabs: tabs.map((el) => ({name:(el.innerText || el.getAttribute('aria-label') || '').trim(), selected:el.getAttribute('aria-selected')})),
              panels: panels.map((el) => ({text:(el.innerText || '').trim(), hidden:el.hidden || getComputedStyle(el).display === 'none'})),
            };
            """
        )
        tabs = before.get("tabs", [])
        if not tabs:
            return []
        issues: list[dict[str, str]] = []
        if any(not item.get("name") for item in tabs):
            issues.append(cls._issue("desktop", "A tab has no accessible name"))
        if len([item for item in tabs if item.get("selected") == "true"]) != 1:
            issues.append(cls._issue("desktop", "Tablist must have exactly one selected tab"))
        visible_panels = [item for item in before.get("panels", []) if not item.get("hidden")]
        if len(visible_panels) != 1 or not visible_panels[0].get("text"):
            issues.append(cls._issue("desktop", "Tabs need exactly one non-empty visible tabpanel"))
        target = next((index for index, item in enumerate(tabs) if item.get("selected") != "true"), None)
        if target is not None:
            driver.execute_script("document.querySelectorAll('[role=tab]')[arguments[0]].click()", target)
            time.sleep(0.15)
            after = driver.execute_script(
                """
                return {
                  selected:[...document.querySelectorAll('[role=tab]')].map((el) => el.getAttribute('aria-selected')),
                  panels:[...document.querySelectorAll('[role=tabpanel]')].map((el) => ({text:(el.innerText || '').trim(), hidden:el.hidden || getComputedStyle(el).display === 'none'})),
                };
                """
            )
            before_selected = [item.get("selected") for item in tabs]
            if after.get("selected") == before_selected and after.get("panels") == before.get("panels"):
                issues.append(cls._issue("desktop", "Tabs do not react to clicks or change visible content"))
        return issues

    @staticmethod
    def _issue(viewport: str, message: str) -> dict[str, str]:
        return {"path": "index.html", "kind": "browser", "viewport": viewport, "message": message}

    @classmethod
    def _failure(cls, message: str) -> dict[str, Any]:
        return {
            "passed": False, "applicable": True, "skipped": False, "runtime": None,
            "issues": [cls._issue("browser", message)], "viewports": [], "screenshots": [],
        }

    @staticmethod
    def _skipped() -> dict[str, Any]:
        return {
            "passed": True, "applicable": False, "skipped": True, "runtime": None,
            "issues": [], "viewports": [], "screenshots": [],
        }
