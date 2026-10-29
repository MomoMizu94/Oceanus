# Accept a coding task, load the coding agent settings, and display its progress
import argparse
from importlib.resources import files
from pathlib import Path

from agent_core.loop import run_agent
from agent_core.model_client import DEFAULT_MODEL
from agent_core.memory.session import load_session, save_session
from agent_core.guardrails import approve_shell, approve_file
from agent_core.tools.registry import TOOL_REGISTRY
from agent_core.search_approval import approve_search
from agent_core.edit_approval import approve_edit
from agent_core.sensitive_approval import approve_sensitive_access
from agent_core.filename_approval import approve_discovery
from agent_core.instructions import build_system_prompt
from agent_core.web_approval import approve_web_search
from agent_core.fetch_approval import approve_page_fetch


def print_tool_status(tool_name: str, status: str) -> None:
    print(f"\nTool execution ({tool_name}): {status}")

def print_tool_result(tool_name: str, result: str) -> None:
    print(f"\nTool result ({tool_name}):")
    print(result)

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
    tool_options = parser.add_mutually_exclusive_group()

    tool_options.add_argument(
        "--tools",
        nargs="+",
        action="extend",
        choices=sorted(TOOL_REGISTRY),
        help="Allow only these tools. Omit to allow all registered tools."
    )
    tool_options.add_argument(
        "--no-tools",
        action="store_true",
        help="Disable all tool execution for this run."
    )

    # Reads arguments
    arguments = parser.parse_args()

    if arguments.no_tools:
        allowed_tools = set()
    elif arguments.tools is not None:
        allowed_tools = set(arguments.tools)
    else:
        allowed_tools = None

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
    try:
        # Built-in instructions
        base_prompt = (
            files("agents.coding").joinpath("system_prompt.md").read_text(encoding="utf-8")
        )
        # .md files get built into the prompt
        coding_prompt = build_system_prompt(base_prompt)
    except (OSError, ValueError) as error:
        parser.error(f"Could not load instructions: {error}")

    print("Model: ", arguments.model)

    # Reply
    answer = run_agent(
        arguments.task,
        max_turns=arguments.max_turns,
        system_prompt=coding_prompt,
        model=arguments.model,
        conversation_history=conversation_history,
        on_progress=print,
        on_approval=approve_shell,
        on_file_approval=approve_file,
        on_tool_result=print_tool_result,
        allowed_tools=allowed_tools,
        on_tool_status=print_tool_status,
        on_search_approval=approve_search,
        on_edit_approval=approve_edit,
        on_sensitive_approval=approve_sensitive_access,
        on_discovery_approval=approve_discovery,
        on_web_approval=approve_web_search,
        on_fetch_approval=approve_page_fetch,
    )
    print("Agent:", answer)

    if arguments.save is not None:
        try:
            save_session(arguments.save, conversation_history)
        except (OSError, ValueError) as error:
            parser.error(f"Could not save session: {error}")

        print("Session saved succesfully:", arguments.save)
