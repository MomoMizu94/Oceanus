import json
from pathlib import Path


def save_session(path: str | Path, messages: list[dict]) -> None:
    """ Save conversation messages as json """
    # message list into json
    text = json.dumps(messages, ensure_ascii=False, indent=2)
    # Writes text into a json conversation file
    Path(path).write_text(text + "\n", encoding="utf-8")

def load_session(path: str | Path) -> list[dict]:
    """ Load conversation from json file """
    # Converts saved json conversation into a list
    text = Path(path).read_text(encoding="utf-8")
    # Json parsing
    messages = json.loads(text)

    # Checks if result is not a list
    if not isinstance(messages, list):
        raise ValueError("Session must contain a list!")

    for message in messages:
        # Checks if every item in result is not a dictionary
        if not isinstance(message, dict):
            raise ValueError("Each message must be a dictionary!")

    return messages