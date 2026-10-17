import json
import os
from pathlib import Path
from fnmatch import fnmatchcase
from collections.abc import Callable

from agent_core.guardrails import resolve_workspace_path
from agent_core.sensitive_paths import sensitive_path_reason


MAX_ENTRIES = 1000
MAX_MATCHES = 100
MAX_SKIPPED = 100
EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", "node_modules", "build", "dist"
}
MAX_DEPTH = 10


def find_files(path: str, pattern: str = "*", *, workspace_root: Path, on_discovery_approval: Callable[[str, str], bool] | None = None) -> str:
    if not isinstance(pattern, str) or not 1<= len(pattern) <= 100:
        raise ValueError("Pattern must contain between 1 and 100 characters")

    for char in ("/", "\\", "\n", "\r"):
        if char in pattern:
            raise ValueError("Pattern must be a filename")

    def request_approval(operation: str, resolved_path: str) -> bool:
        # Called for external directories
        if on_discovery_approval is None:
            return False

        decision = on_discovery_approval(resolved_path, pattern)
        return decision is True

    directory = resolve_workspace_path(
        path,
        workspace_root,
        operation="search",
        on_file_approval=request_approval,
    )

    result = {
        "path": str(directory),
        "pattern": pattern,
        "matches": [],
        "skipped": [],
        "truncated": False
    }

    scanned_entries = 0
    pending = [(directory, 1)]
    stop_search = False

    # Visits through dirs until list is empty or hits the limits
    while pending and not stop_search:
        current_directory, depth = pending.pop()
        
        with os.scandir(current_directory) as entries:
            for entry in entries:
                # Scanned entry checks
                if scanned_entries >= MAX_ENTRIES:
                    result["truncated"] = True
                    stop_search = True
                    break

                if len(result["matches"]) >= MAX_MATCHES:
                    result["truncated"] = True
                    stop_search = True
                    break

                if len(result["skipped"]) >= MAX_SKIPPED:
                    result["truncated"] = True
                    stop_search = True
                    break

                scanned_entries += 1
                entry_path = Path(entry.path)
                relative_path = entry_path.relative_to(directory).as_posix()

                # Skipping symlinks, protected paths and non-files with explanations
                if entry.is_symlink():
                    result["skipped"].append({
                        "path": relative_path,
                        "reason": "symlink"
                    })
                    continue

                if sensitive_path_reason(entry_path) is not None:
                    result["skipped"].append({
                        "path": relative_path,
                        "reason": "sensitive path"
                    })
                    continue

                if entry.is_dir(follow_symlinks=False):
                    if entry.name in EXCLUDED_DIRS:
                        result["skipped"].append({
                            "path": relative_path,
                            "reason": "excluded directory"
                        })
                    elif depth >= MAX_DEPTH:
                        result["truncated"] = True
                    else:
                        # Allowed sub dir added to list with its depth number
                        pending.append((entry_path, depth + 1))
                    continue

                if not entry.is_file(follow_symlinks=False):
                    continue

                if fnmatchcase(entry.name, pattern):
                    result["matches"].append(relative_path)

        result["matches"].sort()

    return json.dumps(result)