# Implement run_shell and return command output after manual approval
import subprocess
import json
from pathlib import Path
from collections.abc import Callable

from agent_core.output_store import save_output_log


MAX_SHELL_PREVIEW_CHARS = 4000


def _preview_output(text: str) -> str:
    """ Return short preview and retain longer output in local logs """
    if len(text) <= MAX_SHELL_PREVIEW_CHARS:
        return text

    preview = text[:MAX_SHELL_PREVIEW_CHARS]
    try:
        log = save_output_log(text)
    except (OSError, UnicodeError) as error:
        return (
            f"{preview}\n[Output preview truncated. "
            f"Log unavailable ({type(error).__name__}); "
            "omitted output was not retained.]"
        )

    log["next_offset"] = (
        MAX_SHELL_PREVIEW_CHARS
        if log["stored_chars"] > MAX_SHELL_PREVIEW_CHARS else None
    )
    result = (
        f"{preview}\n[Output preview truncated.]\n"
        f"Output log: {json.dumps(log, ensure_ascii=False)}"
    )
    if log["truncated"]:
        result += "\n[Output beyond stored_chars was not retained.]"

    return result


def run_shell(command: str, *, on_approval: Callable[[str, str], bool] | None = None) -> str:
    """ Run approved shell commmand after approval """
    working_directory = str(Path.cwd())

    if on_approval is None:
        return "Command denied: no approval handler was supplied. Nothing was executed."
    if on_approval(command, working_directory) is not True:
        return "Command denied by the user. Nothing was executed."

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False
        )
    except subprocess.TimeoutExpired:
        return "Command timed out after 1 minute."

    return(
        f"Exit code: {completed.returncode}\n"
        f"STDOUT:\n{_preview_output(completed.stdout)}\n"
        f"STDERR:\n{_preview_output(completed.stderr)}"
    )