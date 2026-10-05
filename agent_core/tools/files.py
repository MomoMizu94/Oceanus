# File-related tools
from pathlib import Path
from collections.abc import Callable
import json

from agent_core.guardrails import resolve_workspace_path


MAX_READ_CHARS = 16000

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


def read_file_page(
        path: str,
        offset: int = 0,
        max_chars: int = 8000,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None
) -> str:
    """ Read a page of text using a character offset """

    # Validation
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_READ_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_READ_CHARS}")

    file_path = resolve_workspace_path(path, workspace_root, operation="read", on_file_approval=on_file_approval)

    # Skip to requested character offset
    with file_path.open("r", encoding="utf-8", newline="") as source:
        remaining = offset
        while remaining:
            skipped = source.read(min(remaining, 4096))
            if not skipped:
                raise ValueError("offset is beyond the end of the file")
            remaining -= len(skipped)

        text = source.read(max_chars + 1)

    truncated = len(text) > max_chars
    content = text[:max_chars]

    # Return content and its continuation offset
    return json.dumps({
        "path": str(file_path),
        "offset": offset,
        "content": content,
        "truncated": truncated,
        "next_offset": offset + len(content) if truncated else None,
    }, ensure_ascii=False)