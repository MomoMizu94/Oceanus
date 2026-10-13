# Implement run_shell and return command output after manual approval
import json
from pathlib import Path
from collections.abc import Callable

from agent_core.output_store import save_output_log
from agent_core.shell_capture import capture_shell


MAX_SHELL_PREVIEW_CHARS = 4000


def _preview_output(output: dict, *, capture_incomplete: bool = False) -> str:
    """ Format captured output without losing its truncation info """
    text = output["content"]
    capture_note = ""
    if output["truncated"]:
        capture_note = (
            f"\n[Capture truncated: retained {output['stored_chars']} of "
            f"{output['original_chars']} received characters. "
            "Remaining output was discarded.]"
        )

    preview = text[:MAX_SHELL_PREVIEW_CHARS]
    if len(text) <= MAX_SHELL_PREVIEW_CHARS:
        return preview + capture_note

    try:
        log = save_output_log(text)
    except (OSError, UnicodeError) as error:
        return (
            f"{preview}{capture_note}\n[Output preview truncated. "
            f"Log unavailable ({type(error).__name__}); "
            "omitted output was not retained.]"
        )

    # Longer output preservation
    log["original_chars"] = output["original_chars"]
    log["truncated"] = output["truncated"] or log["truncated"]
    log["capture_incomplete"] = capture_incomplete
    log["next_offset"] = (
        MAX_SHELL_PREVIEW_CHARS
        if log["stored_chars"] > MAX_SHELL_PREVIEW_CHARS else None
    )

    result = (
        f"{preview}{capture_note}\n[Output preview truncated.]\n"
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

    captured = capture_shell(command, cwd=working_directory, timeout=60)

    if captured["timed_out"]:
        status = (
            "Command timed out after 60 seconds. Partial output follows.\n"
            f"Direct process exit code: {captured['returncode']}"
        )
    else:
        status = f"Exit code: {captured['returncode']}"

    incomplete = captured["capture_incomplete"]
    if incomplete:
        status += (
            "\nOutput collection stopped before EOF. Counts cover only received text; "
            "saved logs may be incomplete. Detached processes may still be running."
        )

    return (
        f"{status}\n"
        f"STDOUT:\n{_preview_output(captured['stdout'], capture_incomplete=incomplete)}\n"
        f"STDERR:\n{_preview_output(captured['stderr'], capture_incomplete=incomplete)}"
    )