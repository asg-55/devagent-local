from __future__ import annotations

from typing import Any

import requests


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def models(self, timeout: int = 5) -> list[dict[str, Any]]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=timeout)
            response.raise_for_status()
            return response.json().get("models", [])
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama is unavailable: {exc}") from exc

    def model_names(self) -> list[str]:
        return [str(model.get("name")) for model in self.models() if model.get("name")]

    def generate(self, model: str, prompt: str, num_predict: int, timeout: int = 300) -> str:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": num_predict, "temperature": 0.2, "top_p": 0.9},
        }
        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=timeout)
            response.raise_for_status()
            return str(response.json().get("response", ""))
        except requests.RequestException as exc:
            raise OllamaError(f"Model request failed: {exc}") from exc

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        num_predict: int = 4096,
        timeout: int = 300,
        num_ctx: int = 8192,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"num_predict": num_predict, "num_ctx": num_ctx, "temperature": 0.1, "top_p": 0.9},
        }
        try:
            response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
            response.raise_for_status()
            message = response.json().get("message", {})
            if not isinstance(message, dict):
                raise OllamaError("Ollama returned an invalid chat message")
            return message
        except requests.RequestException as exc:
            raise OllamaError(f"Model chat request failed: {exc}") from exc
