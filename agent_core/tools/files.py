# File-related tools
from pathlib import Path
from collections.abc import Callable

from agent_core.guardrails import resolve_workspace_path


def read_file(path: str, *, workspace_root: Path, on_file_approval: Callable[[str, str], bool] | None = None) -> str:
    """ Read UTF-8 file, request approval for eternal access. """
    file_path = resolve_workspace_path(path, workspace_root, operation="read", on_file_approval=on_file_approval)
    return file_path.read_text(encoding="utf-8")


def write_file(path: str, content: str, *, workspace_root: Path, on_file_approval: Callable[[str, str], bool] | None = None) -> str:
    """ Create or overwrite a UTF-8 file, request approval for access. """
    file_path = resolve_workspace_path(path, workspace_root, operation="write", on_file_approval=on_file_approval)
    text_written = file_path.write_text(
        content,
        encoding="utf-8"
    )
    return f"Wrote {text_written} into {file_path}."