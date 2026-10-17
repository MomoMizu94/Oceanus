from agent_core.tools.discovery import (
    EXCLUDED_DIRS,
    MAX_DEPTH,
    MAX_ENTRIES,
    MAX_MATCHES,
    MAX_SKIPPED
)


def describe_discovery(path: str, pattern: str) -> str:
    excluded_names = ", ".join(sorted(EXCLUDED_DIRS))

    return(
        f"Resolved directory: {path!r}\n"
        f"Filename pattern, case-sensitive: {pattern!r}\n"
        "Scope: recursive filename search; does not read file contents.\n"
        f"Maximum depth: {MAX_DEPTH}. Direct children count as depth 1.\n"
        f"Limits across the whole search: {MAX_ENTRIES} scanned entries, "
        f"{MAX_MATCHES} matches, {MAX_SKIPPED} reported skips.\n"
        "Skips child symlinks and protected paths.\n"
        f"Excluded child directories: {excluded_names}.\n"
        "Other hidden names are included; .gitignore is not applied.\n"
        "Returned paths are sent to the model and may be saved in the session.\n"
        "Allows this search only, not later file reads or modifications."
    )

def approve_discovery(path: str, pattern: str) -> bool:
    print("\nExternal filename search requested:")
    print(describe_discovery(path, pattern))

    try:
        answer = input("Allow this filename search? [y/N]: ")
    except EOFError:
        return False

    return answer.strip().lower() == "y"