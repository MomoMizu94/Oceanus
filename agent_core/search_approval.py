from agent_core.tools.search import (
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_MATCHES,
    MAX_LINE_CHARS,
    EXCLUDED_DIRS,
    MAX_DEPTH,
    MAX_SKIPPED
)


def describe_search(path: str, query: str, pattern: str) -> str:
    """ Describe the exact scope of one external content seach """
    excluded_names = ", ".join(sorted(EXCLUDED_DIRS))
    
    return (
        f"Resolved directory: {path!r}\n"
        f"Literal query, case-sensitive: {query!r}\n"
        f"Filename pattern: {pattern!r}\n"
        "Scope: recursive content search below this directory.\n"
        "Reads matching UTF-8 files; skips protected paths and child symlinks.\n"
        f"Excluded child directories: {excluded_names}.\n"
        "Other matching hidden files are included; .gitignore is not applied.\n"
        f"Maximum depth: {MAX_DEPTH}. Direct children count as depth 1.\n"
        f"Limits across the whole search: {MAX_ENTRIES} scanned entries, "
        f"{MAX_MATCHES} matching lines, {MAX_SKIPPED} reported skips.\n"
        f"File limits: {MAX_FILE_BYTES} bytes per file and "
        f"{MAX_LINE_CHARS} displayed characters per matching line.\n"
        "Returned text and paths reach the model and may be saved in the session.\n"
        "Allows this search only, not later whole-file reads or modifications."
    )

def approve_search(path: str, query: str, pattern: str) -> bool:
    print("\nExternal content search requested:")
    print(describe_search(path, query, pattern))

    try:
        answer = input("Allow this search? [y/N]: ")
    except EOFError:
        return False

    return answer.strip().lower() == "y"