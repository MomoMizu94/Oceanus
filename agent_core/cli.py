# Accept a coding task, load the coding agent settings, and display its progress
import argparse
from importlib.resources import files

from agent_core.loop import run_agent


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

    # Reads arguments
    arguments = parser.parse_args()

    # Guardrails
    if not arguments.task.strip():
        parser.error("Task must not be empty!")
    if arguments.max_turns < 1:
        parser.error("--max-turns must be at least 1!")

    # Prompt laoding
    coding_prompt = (
        files("agents.coding").joinpath("system_prompt.md").read_text(encoding="utf-8")
    )

    # Reply
    answer = run_agent(
        arguments.task,
        max_turns=arguments.max_turns,
        system_prompt=coding_prompt
    )
    print("Agent:", answer)