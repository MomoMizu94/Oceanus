"""Pair each tool's Python function with its model-facing schema."""

from collections.abc import Callable

from agent_core.tools.files import read_file, write_file
from agent_core.tools.shell import run_shell


TOOL_REGISTRY = {
    "read_file": {
        # Keep the callable locally; only the schema is sent to the model.
        "handler": read_file,
        "schema": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a UTF-8 text file.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path, either absolute or relative to the workspace directory. "
                                "Access outside the workspace requires explicit user approval "
                                "for this operation. Access is denied if approval is unavailable."
                            ),
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
    },
    "write_file": {
        "handler": write_file,
        "schema": {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": (
                    "Create or overwrite a UTF-8 text file. "
                    "Replaces the entire contents of an existing file. "
                    "The parent directory must already exist."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path, either absolute or relative to the workspace directory. "
                                "Access outside the workspace requires explicit user approval "
                                "for this operation. Access is denied if approval is unavailable."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "The complete text to write to the file.",
                        },
                     },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        },
    },
    "run_shell": {
        "handler": run_shell,
        "schema": {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": (
                    "Request user approval for a shell command, then execute it only "
                    "if approved. Each call requires a separate approval decision. "
                    "Returns the exit code, standard output and standard error. "
                    "Commands time out after 60 seconds. "
                    "If denied, report the denial. Do not retry or bypass it on your own. "
                    "A later explicit user request may initiate a new approval request."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell commmand to execute."
                        }
                    },
                    "required": ["command"],
                    "additionalProperties": False
                }
            }
        }
    },
}


def get_tool_schemas(allowed_tools: set[str] | frozenset[str] | None = None) -> list[dict]:
    """ Return definitions for selected tools in registry order """
    registered_tools = set(TOOL_REGISTRY)
    selected_tools = registered_tools if allowed_tools is None else allowed_tools

    unknown_tools = selected_tools - registered_tools
    if unknown_tools:
        raise ValueError(f"Unknown allowed tools: {', '.join(sorted(unknown_tools))}")

    return [
        tool["schema"]
        for name, tool in TOOL_REGISTRY.items()
        if name in selected_tools
    ]


def get_tool_function(name: str) -> Callable[..., str]:
    """ Look up a registered function, raising KeyError for unknown tools """
    return TOOL_REGISTRY[name]["handler"]
