# File-related tools
from pathlib import Path
from collections.abc import Callable
import json

from agent_core.guardrails import resolve_workspace_path


MAX_READ_CHARS = 16000
MAX_READ_LINES = 200

def read_file(
        path: str,
        *,
        workspace_root: Path, on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> str:
    """ Read UTF-8 file, request approval for eternal access. """
    file_path = resolve_workspace_path(path, workspace_root, operation="read", on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval)
    return file_path.read_text(encoding="utf-8")


def write_file(
        path: str,
        content: str,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> str:
    """ Create or overwrite a UTF-8 file, request approval for access. """
    file_path = resolve_workspace_path(path, workspace_root, operation="write", on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval)
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
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> str:
    """ Read a page of text using a character offset """

    # Validation
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_READ_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_READ_CHARS}")

    file_path = resolve_workspace_path(path, workspace_root, operation="read", on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval)

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

def read_file_lines(
        path: str,
        start_line: int = 1,
        end_line: int = 100,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> str:
    """ Read an inclusive line range. Preserve original line endings """

    # Range validation
    if type(start_line) is not int or start_line < 1:
        raise ValueError("start_line must be a positive integer.")

    if type(end_line) is not int or end_line < start_line:
        raise ValueError("end_line must be an integer at least start_line")

    if end_line - start_line + 1 > MAX_READ_LINES:
        raise ValueError(f"Request at most {MAX_READ_LINES} lines per read operation")

    file_path = resolve_workspace_path(
        path,
        workspace_root,
        operation="read",
        on_file_approval=on_file_approval,
        on_sensitive_approval=on_sensitive_approval
    )

    lines = []
    content_chars = 0

    with file_path.open("r", encoding="utf-8", newline="") as source:
        # Start from the beginning to count lines
        for number in range(1, end_line + 1):
            read_limit = MAX_READ_CHARS
            if number >= start_line:
                read_limit = MAX_READ_CHARS - content_chars

            text = source.readline(read_limit + 1)
            if not text:
                break

            if len(text) > read_limit:
                raise ValueError(
                    "Line-based read exceeds the character limit; "
                    "request fewer or use read_file character paging"
                )

            if number >= start_line:
                lines.append({
                    "number": number,
                    "text": text
                })
                content_chars += len(text)

        # Check whether more content follows after limit
        truncated = bool(source.read(1))

    if not lines and start_line > 1:
        raise ValueError("start_line is beyond the end of the file!")

    last_line = None
    next_line = None

    # Identify where to continue
    if lines:
        last_line = lines[-1]["number"]
        if truncated:
            next_line = last_line + 1

    return json.dumps({
        "path": str(file_path),
        "start_line": start_line,
        "end_line": last_line,
        "lines": lines,
        "truncated": truncated,
        "next_line": next_line
    }, ensure_ascii=False)