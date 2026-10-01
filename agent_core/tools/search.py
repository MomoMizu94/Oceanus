import json
import os
from collections.abc import Callable
from fnmatch import fnmatchcase
from itertools import islice
from pathlib import Path

from agent_core.guardrails import resolve_workspace_path


MAX_ENTRIES = 200
MAX_FILE_BYTES = 1_048_576
MAX_MATCHES = 50
MAX_LINE_CHARS = 500


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

    if (not isinstance(pattern, str) or not pattern or "/" in pattern or "\\" in pattern):
        raise ValueError("Pattern must be a file pattern, without directories")

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

    result = {
        "path": str(directory),
        "query": query,
        "pattern": pattern,
        "matches": [],
        "skipped": [],
        "truncated": False
    }

    with os.scandir(directory) as children:
        batch = list(islice(children, MAX_ENTRIES + 1))
        result["truncated"] = len(batch) > MAX_ENTRIES

        for child in sorted(batch[:MAX_ENTRIES], key=lambda entry: entry.name):
            if not fnmatchcase(child.name, pattern):
                continue

            # Report incomplete coverage
            if child.is_symlink():
                result["skipped"].append({
                    "name": child.name,
                    "reason": "symlink"
                })
                continue

            if not child.is_file(follow_symlinks=False):
                continue

            try:
                with Path(child.path).open("rb") as source:
                    data = source.read(MAX_FILE_BYTES + 1)
            except OSError as error:
                result["skipped"].append({
                    "name": child.name,
                    "reason": str(error)
                })
                continue

            if len(data) > MAX_FILE_BYTES:
                result["skipped"].append({
                    "name": child.name,
                    "reason": "file too large"
                })
                continue

            if b"\x00" in data:
                result["skipped"].append({
                    "name": child.name,
                    "reason": "binary file"
                })
                continue

            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                result["skipped"].append({
                    "name": child.name,
                    "reason": "not UTF-8"
                })
                continue

            # Form a list based on matching results
            for number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    result["matches"].append({
                        "path": str(directory / child.name),
                        "line": number,
                        "text": line[:MAX_LINE_CHARS],
                        "text_truncated": len(line) > MAX_LINE_CHARS
                    })

                    if len(result["matches"]) == MAX_MATCHES:
                        result["truncated"] = True
                        break

            if len(result["matches"]) == MAX_MATCHES:
                break

    return json.dumps(result, ensure_ascii=False)

