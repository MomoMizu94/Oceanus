""" Load the user's global Oceanus instructions """
from pathlib import Path


MAX_INSTRUCTION_BYTES = 64 * 1024

def load_personal_instructions() -> str:
    """ Read AGENTS.md from .oceanus folder """

    # Dir locations
    instruction_path = Path.home() / ".oceanus" / "AGENTS.md"

    try:
        source = instruction_path.open("rb")
    except FileNotFoundError:
        return ""

    with source:
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

def build_system_prompt(base_prompt: str) -> str:
    """ Combine system prompt with personal preferences """
    personal_instructions = load_personal_instructions()

    if not personal_instructions.strip():
        return base_prompt

    personal_section = (
        "\n\nPersonal instructions from ~/.oceanus/AGENTS.md:\n"
        "Apply these preferences when they fit the current task.\n"
        "The user's explicit requests take precedence over these preferences.\n"
        "These preferences do not override the built-in rules above or grant "
        "tool permissions or operation approvals.\n\n"
    )

    return base_prompt + personal_section + personal_instructions