from pathlib import Path


def describe_tool_outcome(tool_name: str, arguments, outcome: dict | None) -> str:
    """Summarize recorded results without echoing commands, queries, or file contents."""
    if outcome is None:
        return f"{tool_name} returned a result"
    status = outcome["status"]
    if status != "success":
        return f"{tool_name}: {status}"
    if isinstance(arguments, dict):
        path = arguments.get("path")
        if isinstance(path, str) and path:
            filename = Path(path).name[:80]
            if tool_name in ("read_file", "read_file_lines"):
                return f"read {filename!r}"
            return f"{tool_name} succeeded for {filename!r}"
    return f"{tool_name} succeeded"
