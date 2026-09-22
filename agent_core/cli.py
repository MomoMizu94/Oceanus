# Accept a coding task, load the coding agent settings, and display its progress
import argparse
from importlib.resources import files
from pathlib import Path

from agent_core.loop import run_agent
from agent_core.model_client import DEFAULT_MODEL
from agent_core.memory.session import load_session, save_session
from agent_core.guardrails import approve_shell


def main() -> None:
    """ Read from CLI and run the coding agent. """
    parser = argparse.ArgumentParser(description="Run Oceanus on a coding task.")
    parser.add_argument("task", help="The coding task, enclosed in quotes.")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="Maximum number of turns allowed (default: 10)."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LiteLLM model identifier (default: {DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--save",
        type=Path,
        help="Save the updated conversation to JSON file."
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="Load a conversation from JSON file before the task."
    )

    # Reads arguments
    arguments = parser.parse_args()

    # Guardrails
    if not arguments.task.strip():
        parser.error("Task must not be empty!")
    if not arguments.model.strip():
        parser.error("--model must not be empty!")
    if arguments.max_turns < 1:
        parser.error("--max-turns must be at least 1!")

    # Fresh run -> empty list; resumed run -> load_session()
    conversation_history = []
    if arguments.resume is not None:
        try:
            conversation_history = load_session(arguments.resume)
        except (OSError, ValueError) as error:
            parser.error(f"Couldn't load the session: {error}")

    # Prompt laoding
    coding_prompt = (
        files("agents.coding").joinpath("system_prompt.md").read_text(encoding="utf-8")
    )

    print("Model: ", arguments.model)

    # Reply
    answer = run_agent(
        arguments.task,
        max_turns=arguments.max_turns,
        system_prompt=coding_prompt,
        model=arguments.model,
        conversation_history=conversation_history,
        on_progress=print,
        on_approval=approve_shell
    )
    print("Agent:", answer)

    if arguments.save is not None:
        try:
            save_session(arguments.save, conversation_history)
        except (OSError, ValueError) as error:
            parser.error(f"Could not save session: {error}")

        print("Session saved succesfully:", arguments.save)