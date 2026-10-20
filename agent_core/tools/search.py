import json
import os
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path

from agent_core.guardrails import resolve_workspace_path
from agent_core.sensitive_paths import sensitive_path_reason
from agent_core.tools.discovery import EXCLUDED_DIRS


MAX_ENTRIES = 200
MAX_FILE_BYTES = 1_048_576
MAX_MATCHES = 50
MAX_LINE_CHARS = 500
MAX_DEPTH = 10
MAX_SKIPPED = 100


def _read_search_text(path: Path) -> str:
    """ Read size-limited utf-8 file after access checks """
    with path.open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)

    if len(data) > MAX_FILE_BYTES:
        raise ValueError("The file's too large!")

    if b"\x00" in data:
        raise ValueError("The file's a binary file!")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("The file's not utf-8 file!") from None

    return text

def _collect_search_files(directory: Path, pattern: str) -> dict:
    """ Collect potential candidates below an access-checked root without reading file contents """
    result = {
        "files": [],
        "skipped": [],
        "truncated": False
    }
    pending = [(directory, 1)]
    scanned_entries = 0
    stop_search = False

    # Visits through dirs until list is empty or hits the limits
    while pending and not stop_search:
        current_directory, depth = pending.pop()

        with os.scandir(current_directory) as entries:
            for entry in entries:
                if scanned_entries >= MAX_ENTRIES:
                    result["truncated"] = True
                    stop_search = True
                    break

                if len(result["skipped"]) >= MAX_SKIPPED:
                    result["truncated"] = True
                    stop_search = True
                    break

                scanned_entries += 1
                entry_path = Path(entry.path)
                relative_name = entry_path.relative_to(directory).as_posix()
                skip_reason = None

                # Skipping symlinks, protected paths and non-files with explanations
                if entry.is_symlink():
                    skip_reason = "symlink"

                elif entry.is_dir(follow_symlinks=False):
                    if sensitive_path_reason(entry_path) is not None:
                        skip_reason = "sensitive directory"
                    elif entry.name in EXCLUDED_DIRS:
                        skip_reason = "excluded directory"
                    elif depth >= MAX_DEPTH:
                        result["truncated"] = True
                    else:
                        pending.append((entry_path, depth + 1))

                elif entry.is_file(follow_symlinks=False):
                    if not fnmatchcase(entry.name, pattern):
                        continue

                    if sensitive_path_reason(entry_path) is not None:
                        skip_reason = "sensitive file"
                    else:
                        result["files"].append(entry_path)

                if skip_reason is not None:
                    result["skipped"].append({
                        "name": relative_name,
                        "reason": skip_reason
                    })

    result["files"].sort()
    return result

def search_files(
        path: str,
        query: str,
        pattern: str = "*",
        *,
        workspace_root: Path,
        on_search_approval: Callable[[str, str, str], bool] | None = None
) -> str:
    """ Search immediate UTF-8 files for literal, case-sensitive text """

    # Search validation
    if not isinstance(query, str) or not 1 <= len(query) <= 1000:
        raise ValueError("Query must contain between 1 and 1000 characters")

    if "\n" in query or "\r" in query:
        raise ValueError("Query must fit on one line")

    if not isinstance(pattern, str) or not 1 <= len(pattern) <= 100:
        raise ValueError("Pattern must contain between 1 and 100 characters")

    for char in ("/", "\\", "\n", "\r"):
        if char in pattern:
            raise ValueError("Pattern must be a filename")

    def request_approval(operation: str, resolved_path: str) -> bool:
        if on_search_approval is None:
            return False
        return on_search_approval(resolved_path, query, pattern) is True

    directory = resolve_workspace_path(
        path,
        workspace_root,
        operation="search",
        on_file_approval=(request_approval if on_search_approval is not None else None)
    )

    collected = _collect_search_files(directory, pattern)

    result = {
        "path": str(directory),
        "query": query,
        "pattern": pattern,
        "matches": [],
        "skipped": collected["skipped"],
        "truncated": collected["truncated"]
    }

    for file_path in collected["files"]:
        if len(result["skipped"]) >= MAX_SKIPPED:
            result["truncated"] = True
            break

        try:
            text = _read_search_text(file_path)
        except (OSError, ValueError) as error:
            relative_name = file_path.relative_to(directory).as_posix()
            result["skipped"].append({
                "name": relative_name,
                "reason": str(error)
            })
            continue

        for number, line in enumerate(text.splitlines(), start=1):
            if query in line:
                result["matches"].append({
                    "path": str(file_path),
                    "line": number,
                    "text": line[:MAX_LINE_CHARS],
                    "text_truncated": len(line) > MAX_LINE_CHARS
                })

                if len(result["matches"]) >= MAX_MATCHES:
                    result["truncated"] = True
                    break

        if len(result["matches"]) >= MAX_MATCHES:
            break

    result["skipped"].sort(key=lambda entry: entry["name"])
    return json.dumps(result, ensure_ascii=False)