# Accept a coding task, load the coding agent settings, and display its progress
import argparse
import os
from importlib.resources import files
from pathlib import Path

from agent_core.loop import run_agent, DEFAULT_MAX_TURNS
from agent_core.permissions import PermissionMode, MODE_LABELS, permission_instructions
from agent_core.model_client import DEFAULT_MODEL
from agent_core.memory.recovery import (
    RECOVERY_NOTICE, has_interrupted_tools, load_recovery_session, new_session_path,
)
from agent_core.shell_sandbox import validate_workspace_root
from agent_core.guardrails import approve_shell, approve_file
from agent_core.tools.registry import TOOL_REGISTRY
from agent_core.search_approval import approve_search
from agent_core.edit_approval import approve_edit
from agent_core.sensitive_approval import approve_sensitive_access
from agent_core.filename_approval import approve_discovery
from agent_core.instructions import build_system_prompt
from agent_core.skills import discover_skills, prepare_skill_prompt
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
    parser.add_argument("task", nargs="?", help="The coding task. Use single quotes around prompts containing $skill-name.")
    parser.add_argument("--list-skills", action="store_true", help="List local skills without calling a model.")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help=f"Maximum number of turns allowed (default: {DEFAULT_MAX_TURNS})."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LiteLLM model identifier (default: {DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--save",
        type=Path,
        help="Autosave checkpoints to this JSON file instead of the default session path."
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="Resume a JSON session and autosave back to it unless --save selects another file."
    )
    parser.add_argument(
        "--workspace", type=Path,
        help=(
            "Project folder. Its root AGENTS.md is loaded before each task. "
            "Required when resuming a session without workspace metadata."
        )
    )

    parser.add_argument(
        "--permission-mode", choices=[mode.value for mode in PermissionMode],
        default=PermissionMode.ASK.value,
        help="Approval policy: ask (default), auto, full (host access), or read-only.",
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

    if arguments.list_skills:
        try:
            skills, warnings = discover_skills()
        except (OSError, ValueError) as error:
            parser.error(f"Could not discover skills: {error}")
        if not skills:
            print("No local skills found. Put skill folders in ~/.oceanus/skills.")
        for skill in skills:
            print(f"${skill.name}: {skill.description}\n  {skill.path}")
        for warning in warnings:
            print(f"Skill notice: {warning}")
        return

    if arguments.no_tools:
        allowed_tools = set()
    elif arguments.tools is not None:
        allowed_tools = set(arguments.tools)
    else:
        allowed_tools = None

    # Guardrails
    if arguments.task is None or not arguments.task.strip():
        parser.error("Task must not be empty!")
    if not arguments.model.strip():
        parser.error("--model must not be empty!")
    if arguments.max_turns < 1:
        parser.error("--max-turns must be at least 1!")

    # Resolve session paths before a workspace change affects relative paths
    try:
        if arguments.save is not None:
            arguments.save = arguments.save.resolve()
        if arguments.resume is not None:
            arguments.resume = arguments.resume.resolve()
    except (OSError, ValueError) as error:
        parser.error(f"Could not resolve session path: {error}")
    workspace = arguments.workspace

    # Resume messages together with their original workspace
    conversation_history = []
    active_messages = []
    if arguments.resume is not None:
        try:
            conversation_history, active_messages, saved_workspace = load_recovery_session(arguments.resume)
            if saved_workspace is None and workspace is None:
                parser.error("This session has no project folder. Choose one with --workspace.")
            if saved_workspace is not None:
                if workspace is not None and workspace.expanduser().resolve() != saved_workspace.resolve():
                    parser.error("This session belongs to another project. Start a new conversation to change projects.")
                workspace = saved_workspace
        except (OSError, ValueError) as error:
            parser.error(f"Couldn't load the session: {error}")

    if workspace is not None:
        try:
            workspace = validate_workspace_root(workspace)
            os.chdir(workspace)
        except (OSError, ValueError) as error:
            parser.error(f"Could not open project: {error}")
    workspace = Path.cwd().resolve()

    # Prompt laoding
    try:
        # Built-in instructions
        base_prompt = (
            files("agents.coding").joinpath("system_prompt.md").read_text(encoding="utf-8")
        )
        # .md files get built into the prompt
        coding_prompt = build_system_prompt(base_prompt, workspace=workspace)
        skill_tools = TOOL_REGISTRY if allowed_tools is None else allowed_tools
        coding_prompt, skill_notices = prepare_skill_prompt(
            coding_prompt, arguments.task, available_tools=skill_tools, workspace=workspace,
        )
    except (OSError, ValueError) as error:
        parser.error(f"Could not load instructions: {error}")

    permission_mode = PermissionMode(arguments.permission_mode)
    coding_prompt += permission_instructions(permission_mode)
    print("Model: ", arguments.model)
    print("Permissions:", MODE_LABELS[permission_mode])
    for notice in skill_notices:
        print(notice)

    if arguments.save is not None:
        checkpoint_path = arguments.save
    elif arguments.resume is not None:
        checkpoint_path = arguments.resume
    else:
        checkpoint_path = new_session_path()
    print("Recovery checkpoints:", checkpoint_path)
    if has_interrupted_tools(conversation_history) or has_interrupted_tools(active_messages):
        print(RECOVERY_NOTICE)

    # Reply
    answer = run_agent(
        arguments.task,
        max_turns=arguments.max_turns,
        permission_mode=permission_mode,
        system_prompt=coding_prompt,
        model=arguments.model,
        conversation_history=conversation_history,
        active_messages=active_messages,
        checkpoint_path=checkpoint_path,
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
