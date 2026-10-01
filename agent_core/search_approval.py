from agent_core.tools.search import (
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_MATCHES,
    MAX_LINE_CHARS
)


def describe_search(path: str, query: str, pattern: str) -> str:
    """ Describe the exact scope of one external content seach """
    return (
        f"Resolved directory: {path!r}\n"
        f"Literal query, case-sensitive: {query!r}\n"
        f"Filename pattern: {pattern!r}\n"
        "Scope: immediate files only; no recursion or child symlink following.\n"
        "Reads matching UTF-8 files to find lines containing the query.\n"
        "Matching hidden files are included; .gitignore is not applied.\n"
        f"Limits: {MAX_ENTRIES} directory entries, {MAX_FILE_BYTES} bytes per file, "
        f"{MAX_MATCHES} matching lines, {MAX_LINE_CHARS} displayed characters per line.\n"
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