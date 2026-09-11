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
                                "File path, either absolute or relative to "
                                "the directory where the agent was launched."
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
                                "File path, either absolute or relative to "
                                "the directory where the agent was launched."
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
                    "Run a shell command in the agent's working directory "
                    "after manual user approval. Returns the exit code, "
                    "standard output and standard error. "
                    "Commands time out after 60 seconds. "
                    "If approval is denied, report the denial and do not "
                    "retry or attempt to bypass it."
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


def get_tool_schemas() -> list[dict]:
    """ Return tool definitions to include in a model request """
    return [tool["schema"] for tool in TOOL_REGISTRY.values()]


def get_tool_function(name: str) -> Callable[..., str]:
    """ Look up a registered function, raising KeyError for unknown tools """
    return TOOL_REGISTRY[name]["handler"]
