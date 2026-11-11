import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from copy import deepcopy


def _validate_message_list(messages, label: str) -> None:
    """ Check stored message containers without interpreting their content """
    if not isinstance(messages, list):
        raise ValueError(f"{label} must be a list!")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError(f"Each message in {label} must be a dictionary!")

def _validate_active_context(messages: list[dict], active_messages) -> None:
    _validate_message_list(active_messages, "Active context")
    if messages and not active_messages:
        raise ValueError("A nonempty history needs a nonempty active context!")
    if active_messages and not messages:
        raise ValueError("Active context requires a conversation history!")

def save_session(
    path: str | Path, messages: list[dict], *, workspace: Path | None = None, active_messages: list[dict] | None = None,
) -> None:
    """Save both views together without truncating an existing file"""
    _validate_message_list(messages, "Session")
    document = messages

    if active_messages is not None:
        _validate_active_context(messages, active_messages)
        folder = None
        if workspace is not None:
            folder = str(workspace.resolve())

        document = {
            "version": 2,
            "workspace": folder,
            "messages": messages,
            "active_messages": active_messages,
        }
    elif workspace is not None:
        document = {
            "version": 1,
            "workspace": str(workspace.resolve()),
            "messages": messages,
        }

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
    """ Keep the existing full-history interface for current callers """
    messages, _, workspace = load_session_state(path)
    return messages, workspace


def load_session_state(
    path: str | Path,
) -> tuple[list[dict], list[dict], Path | None]:
    """ Return full history, active context, and the saved workspace """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    messages = document
    workspace = None
    version = None

    if isinstance(document, dict):
        version = document.get("version")
        if type(version) is not int or version not in (1, 2):
            raise ValueError("Unsupported session version")

        folder = document.get("workspace")
        # Version 2 can also store a session without a workspace
        if version == 1 or folder is not None:
            if not isinstance(folder, str) or not Path(folder).is_absolute() or "\x00" in folder:
                raise ValueError("Session workspace must be an absolute folder path")
            workspace = Path(folder)
        messages = document.get("messages")

    _validate_message_list(messages, "Session")

    if version == 2:
        active_messages = document.get("active_messages")
        _validate_active_context(messages, active_messages)
    else:
        # Older sessions use their full history without sharing mutable objects
        active_messages = deepcopy(messages)

    return messages, active_messages, workspace
