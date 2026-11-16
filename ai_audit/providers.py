from __future__ import annotations

from dataclasses import dataclass

import httpx
from django.conf import settings

from ai_audit.schemas import AuditReport


@dataclass
class ProviderInfo:
    name: str
    model: str
    reason: str


class OllamaProvider:
    name = "ollama"

    def __init__(self) -> None:
        self.url = settings.OLLAMA_URL.rstrip("/")
        self.model = settings.OLLAMA_MODEL

    def available(self) -> tuple[bool, str]:
        try:
            tags = httpx.get(f"{self.url}/api/tags", timeout=1.5).json()
        except (httpx.HTTPError, ValueError):
            return False, f"Ollama not reachable at {self.url}"
        names = {m.get("name", "") for m in tags.get("models", [])}
        wanted = {self.model, f"{self.model}:latest"}
        if names & wanted:
            return True, f"Ollama running with {self.model}"
        return False, f"Ollama running but model '{self.model}' not pulled (run: ollama pull {self.model})"

    def generate(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "format": AuditReport.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        r = httpx.post(f"{self.url}/api/chat", json=body, timeout=settings.AI_TIMEOUT_SECONDS)
        r.raise_for_status()
        return r.json()["message"]["content"]


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self.key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL

    def available(self) -> tuple[bool, str]:
        if not self.key:
            return False, "GEMINI_API_KEY is empty"
        return True, f"Gemini API key configured ({self.model})"

    def generate(self, system: str, user: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        r = httpx.post(url, json=body, headers={"x-goog-api-key": self.key}, timeout=settings.AI_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


def select_provider() -> tuple[OllamaProvider | GeminiProvider | None, ProviderInfo]:
    choice = settings.AI_PROVIDER.strip().lower()
    if choice == "deterministic":
        return None, ProviderInfo("deterministic", "template", "AI_PROVIDER=deterministic")
    candidates = {"ollama": [OllamaProvider], "gemini": [GeminiProvider]}.get(
        choice, [OllamaProvider, GeminiProvider])
    reasons = []
    for cls in candidates:
        p = cls()
        ok, why = p.available()
        if ok:
            return p, ProviderInfo(p.name, p.model, why)
        reasons.append(why)
    return None, ProviderInfo("deterministic", "template", " / ".join(reasons) + " -> deterministic fallback")


def describe_provider() -> dict:
    _, info = select_provider()
    return {"name": info.name, "model": info.model, "reason": info.reason}


