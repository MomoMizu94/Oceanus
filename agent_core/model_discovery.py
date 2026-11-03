""" Read installed ollama models from Ollama service """
import json
from urllib.request import urlopen, Request
import os


MAX_MODEL_INFO_BYTES = 1024 * 1024

def list_ollama_models() -> list[str]:
    """ Read installed model tags """
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
        payload = json.load(response)

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Invalid Ollama model list")

    names = set()
    for model in payload["models"]:
        if not isinstance(model, dict):
            raise ValueError("Invalid Ollama model entry.")

        name = model.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid Ollama model name.")

        names.add(name)

    return sorted(names)

def ollama_supports_images(model_name: str) -> bool:
    """ Ask Ollama whether this model reports image support """
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("An Ollama model name is required!")

    api_base = os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434"
    api_base = api_base.strip("/")
    if api_base.endswith("/api/chat"):
        api_base = api_base.removesuffix("/api/chat")

    payload = {"model": model_name, "verbose": False}
    body = json.dumps(payload).encode("utf-8")
    request = Request(f"{api_base}/api/show", data=body, headers={"Content-Type": "application/json"}, method="POST")

    with urlopen(request, timeout=3) as response:
        raw = response.read(MAX_MODEL_INFO_BYTES + 1)
    if len(raw) > MAX_MODEL_INFO_BYTES:
        raise ValueError("Ollama model information is too large!")

    details = json.loads(raw.decode("utf-8"))
    if not isinstance(details, dict):
        raise ValueError("Invalid Ollama model information!")

    capabilities = details.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("Ollama did not provide model capabilities!")

    for capability in capabilities:
        if not isinstance(capability, str):
            raise ValueError("Invalid Ollama capability!")

    return "vision" in capabilities