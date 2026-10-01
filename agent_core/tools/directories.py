import json
import os
from collections.abc import Callable
from itertools import islice
from pathlib import Path

from agent_core.guardrails import resolve_workspace_path


def list_directory(
        path: str,
        *,
        workspace_root: Path,
        max_entries: int = 200,
        on_file_approval: Callable[[str, str], bool] | None = None
) -> str:
    """ List immediate children without reading files or following symlinks """
    # Limits
    if type(max_entries) is not int or not 1 <= max_entries <= 1000:
        raise ValueError("max_entries must be an integer between 1 and 1000")

    # Resolve path & request approval
    directory = resolve_workspace_path(
        path,
        workspace_root,
        operation="list",
        on_file_approval=on_file_approval
    )

    entries = []
    # No recursion
    with os.scandir(directory) as children:
        batch = list(islice(children, max_entries + 1))

        # Categorize entries found
        for child in batch[:max_entries]:
            if child.is_symlink():
                kind = "symlink"
            elif child.is_dir(follow_symlinks=False):
                kind = "directory"
            elif child.is_file(follow_symlinks=False):
                kind = "file"
            else:
                kind = "other"

            entries.append({
                "name": child.name,
                "type": kind
            })

    # Return the list as json
    return json.dumps({
        "path": str(directory),
        "entries": sorted(entries, key=lambda entry: entry["name"]),
        "truncated": len(batch) > max_entries
    }, ensure_ascii=False)