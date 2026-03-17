from __future__ import annotations

import json
import urllib.error
import urllib.request


class OllamaError(RuntimeError):
    """Raised when the local Ollama service is unavailable or returns invalid data."""


def _request_json(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise OllamaError(str(exc)) from exc


def list_models(base_url: str) -> list[str]:
    data = _request_json(f"{base_url.rstrip('/')}/api/tags")
    models = data.get("models", [])
    if not isinstance(models, list):
        raise OllamaError("invalid response payload from Ollama /api/tags")
    names: list[str] = []
    for item in models:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def generate(base_url: str, model: str, prompt: str) -> str:
    data = _request_json(
        f"{base_url.rstrip('/')}/api/generate",
        {"model": model, "prompt": prompt, "stream": False},
    )
    response = data.get("response")
    if not isinstance(response, str):
        raise OllamaError("invalid response payload from Ollama /api/generate")
    return response


def probe(base_url: str, model: str | None = None) -> dict[str, object]:
    models = list_models(base_url)
    selected_model = model or (models[0] if models else None)
    sample = generate(base_url, selected_model, "Reply with exactly: ok") if selected_model else None
    return {"models": models, "selected_model": selected_model, "sample": sample}

