""" User-selected approval policy for one task """

import shlex
from enum import StrEnum
from pathlib import Path

from agent_core.sensitive_paths import sensitive_path_reason


class PermissionMode(StrEnum):
    ASK = "ask"
    AUTO = "auto"
    FULL = "full"
    READ_ONLY = "read-only"


MODE_LABELS = {
    PermissionMode.ASK: "Ask for approval",
    PermissionMode.AUTO: "Approve for me",
    PermissionMode.FULL: "Full Access",
    PermissionMode.READ_ONLY: "Read Only",
}

MODE_DESCRIPTIONS = {
    PermissionMode.ASK: (
        "Workspace edits and sandboxed commands are automatic. "
        "Internet, external and sensitive files ask."
    ),
    PermissionMode.AUTO: (
        "Workspace edits, web tools and simple read commands are automatic. "
        "Other commands, external and sensitive files ask."
    ),
    PermissionMode.FULL: (
        "No per-action approval. Commands run on the host with your filesystem, "
        "environment and network access."
    ),
    PermissionMode.READ_ONLY: (
        "Workspace reads are automatic. "
        "Edits, commands, internet, external and sensitive files ask."
    ),
}


def ordinary_workspace_path(path: str, workspace: Path) -> bool:
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = workspace / requested
    target = requested.resolve()
    return (
        target.is_relative_to(workspace.resolve())
        and sensitive_path_reason(requested) is None
        and sensitive_path_reason(target) is None
    )


def is_simple_read_command(command: str, workspace: Path) -> bool:
    """ Recognize literal read commands; shell syntax and unknown options ask """
    if not isinstance(command, str) or not command.strip():
        return False
    if any(character in command for character in "$`\n\r;&|<>(){}*?[]~"):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    if words == ["pwd"]:
        return True
    options = {
        "ls": {"-l", "-a", "-la", "-al", "-h", "-lah", "--"},
        "cat": {"-n", "--"},
        "head": {"--"},
        "tail": {"--"},
        "wc": {"-l", "-w", "-c", "--"},
    }
    allowed_options = options.get(words[0])
    if allowed_options is None:
        return False
    for argument in words[1:]:
        if argument.startswith("-"):
            if argument not in allowed_options:
                return False
        elif not ordinary_workspace_path(argument, workspace):
            return False
    return True


def automatically_approve(
    mode: PermissionMode, action: str, workspace: Path, *, path: str = "", command: str = "",
) -> bool:
    mode = PermissionMode(mode)
    if mode == PermissionMode.FULL:
        return True
    if action == "edit":
        return mode != PermissionMode.READ_ONLY and ordinary_workspace_path(path, workspace)
    if action == "web":
        return mode == PermissionMode.AUTO
    if action == "shell":
        if mode == PermissionMode.ASK:
            return True
        return mode == PermissionMode.AUTO and is_simple_read_command(command, workspace)
    return False


def permission_instructions(mode: PermissionMode) -> str:
    mode = PermissionMode(mode)
    return (
        f"\n\nCurrent runtime permission mode: {MODE_LABELS[mode]}. "
        f"{MODE_DESCRIPTIONS[mode]} "
        "Only the user can change this setting through the interface. "
        "Call tools normally; the runtime handles any required approvals. "
        "Never bypass a denial. Tool allowlists and input validation still apply."
    )
