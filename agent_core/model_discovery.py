""" Read installed ollama models from Ollama service """
import json
from urllib.request import urlopen


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