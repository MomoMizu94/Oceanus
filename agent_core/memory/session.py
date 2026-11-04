import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def save_session(
    path: str | Path, messages: list[dict], *, workspace: Path | None = None,
) -> None:
    """Save messages and an optional workspace without truncating an existing file"""
    document = messages
    if workspace is not None:
        document = {"version": 1, "workspace": str(workspace.resolve()), "messages": messages}
    text = json.dumps(document, ensure_ascii=False, indent=2)
    path = Path(path)
    temporary_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".session-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(text + "\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

def load_session(path: str | Path) -> list[dict]:
    """Return messages from either a legacy or workspace-aware session"""
    messages, _ = load_session_with_workspace(path)
    return messages


def load_session_with_workspace(path: str | Path) -> tuple[list[dict], Path | None]:
    """Read workspace metadata without changing the process working directory"""
    text = Path(path).read_text(encoding="utf-8")
    messages = json.loads(text)
    workspace = None
    if isinstance(messages, dict):
        if messages.get("version") != 1:
            raise ValueError("Unsupported session version")
        folder = messages.get("workspace")
        if not isinstance(folder, str) or not Path(folder).is_absolute() or "\x00" in folder:
            raise ValueError("Session workspace must be an absolute folder path")
        workspace = Path(folder)
        messages = messages.get("messages")

    if not isinstance(messages, list):
        raise ValueError("Session must contain a list!")

    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Each message must be a dictionary!")

    return messages, workspace
