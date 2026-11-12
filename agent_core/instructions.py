""" Load personal and workspace instructions for each task """
import os
from pathlib import Path
import stat
from typing import BinaryIO


MAX_INSTRUCTION_BYTES = 64 * 1024


def _read_instruction_text(source: BinaryIO, instruction_path: Path) -> str:
    """ Read bounded UTF-8 text without changing its whitespace """
    raw = source.read(MAX_INSTRUCTION_BYTES + 1)

    if len(raw) > MAX_INSTRUCTION_BYTES:
        raise ValueError(
            f"{instruction_path} exceeds the {MAX_INSTRUCTION_BYTES}-byte limit"
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"{instruction_path} must contain utf-8 text"
        ) from error

    if "\x00" in text:
        raise ValueError(
            f"{instruction_path} contains a null character"
        )

    return text


def load_personal_instructions() -> str:
    """ Read AGENTS.md from .oceanus folder """
    instruction_path = Path.home() / ".oceanus" / "AGENTS.md"
    try:
        source = instruction_path.open("rb")
    except FileNotFoundError:
        return ""
    with source:
        return _read_instruction_text(source, instruction_path)


def load_project_instructions(workspace: Path) -> str:
    """ Read only the selected workspace's root AGENTS.md """
    root = workspace.resolve(strict=True)
    instruction_path = root / "AGENTS.md"
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        # Pin the directory and reject symlinks instead of following outside paths
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open("AGENTS.md", flags, dir_fd=directory)
        except FileNotFoundError:
            return ""
        except OSError as error:
            raise OSError(f"Could not open {instruction_path}: {error}") from error
        try:
            # Nonblocking open lets us reject pipes without waiting for a writer
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"{instruction_path} must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                return _read_instruction_text(source, instruction_path)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def build_system_prompt(base_prompt: str, *, workspace: Path | None = None) -> str:
    """ Combine built-in rules, personal preferences, and project conventions """
    personal_instructions = load_personal_instructions()
    prompt = base_prompt

    personal_section = (
        "\n\nPersonal instructions from ~/.oceanus/AGENTS.md:\n"
        "Apply these preferences when they fit the current task.\n"
        "The user's explicit requests take precedence over these preferences.\n"
        "These preferences do not override the built-in rules above or grant "
        "tool permissions or operation approvals.\n\n"
    )

    if personal_instructions.strip():
        prompt += personal_section + personal_instructions

    if workspace is not None:
        project_instructions = load_project_instructions(workspace)
        if project_instructions.strip():
            project_section = (
                "\n\nProject instructions from the selected workspace's root AGENTS.md:\n"
                "Apply these conventions to this workspace. They take precedence over "
                "conflicting personal preferences.\n"
                "The user's explicit requests take precedence over these conventions.\n"
                "These conventions do not override the built-in rules above or grant "
                "tool permissions or operation approvals.\n\n"
            )
            prompt += project_section + project_instructions

    return prompt
