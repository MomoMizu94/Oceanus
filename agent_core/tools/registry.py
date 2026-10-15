"""Pair each tool's Python function with its model-facing schema."""

from collections.abc import Callable

from agent_core.tools.files import read_file_page, write_file, MAX_READ_CHARS
from agent_core.tools.shell import run_shell
from agent_core.tools.directories import list_directory
from agent_core.tools.search import search_files
from agent_core.tools.edits import edit_file


TOOL_REGISTRY = {
    "read_file": {
        # Keep the callable locally; only the schema is sent to the model.
        "handler": read_file_page,
        "schema": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a bounded page of a UTF-8 file. Offsets count characters. "
                    "Returns JSON with path, offset, content, truncated, and next_offset. "
                    "If truncated is true, continue with next_offset to read more. "
                    "Each external read requires approval."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path, either absolute or relative to the workspace directory. "
                                "Access outside the workspace requires approval for each read."
                            ),
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "Character offset. Use 0 to start, "
                                "then the returned next_offset."
                            ),
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_READ_CHARS,
                            "description": (
                                "Maximum content characters to return. "
                                "Use 8000 for a normal page."
                            ),
                        }
                    },
                    "required": ["path", "offset", "max_chars"],
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
                    "Returns the exit code and previews of standard output and standard error. "
                    "Long output includes saved-log metadata. Use read_file with the log path "
                    "and next_offset to retrieve more, subject to file approval. "
                    "If the log is truncated, output beyond stored_chars was not retained. "
                    "Runs non-interactively on POSIX systems. Commands time out after 60 seconds. "
                    "Timeout results include captured partial output. "
                    "capture_incomplete means output collection stopped before EOF. "
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
    "list_directory": {
        "handler": list_directory,
        "schema": {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": (
                    "List immediate child names and types in one directory, "
                    "including hidden entries. Does not recurse, read file "
                    "contents, or follow child symlinks. External directories "
                    "require approval for each call. Returns JSON with path, "
                    "entries, and truncated. If truncated is true, the listing "
                    "is incomplete."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path, absolute or relative to the "
                                "workspace. Use '.' for the workspace."
                            ),
                        },
                        "max_entries": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                            "description": (
                                "Maximum entries to return. "
                                "Use 200 for a normal listing."
                            ),
                        },
                    },
                    "required": ["path", "max_entries"],
                    "additionalProperties": False,
                },
            },
        },
    },
    "search_files": {
        "handler": search_files,
        "schema": {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Search literal, case-sensitive text in immediate UTF-8 files. "
                    "Filter filenames with pattern. No recursion or child symlink following. "
                    "Searches matching hidden files but skips protected files. "
                    "Protected files appear in skipped; .gitignore is not applied. "
                    "External searches require approval for each directory, query, and pattern. "
                    "Returns JSON with matches, skipped files, and truncation flags. "
                    "Do not claim a complete search when files were skipped or results truncated."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path, absolute or relative to the workspace."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Literal text on one line, between 1 and 1000 characters."
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Filename pattern such as *.py or *.txt. "
                                "Use * for all names."
                            ),
                        },
                    },
                    "required": ["path", "query", "pattern"],
                    "additionalProperties": False,
                },
            },
        },
    },
    "edit_file": {
        "handler": edit_file,
        "schema": {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Replace one exact, unique text match in an existing UTF-8 file. "
                    "Read the relevant text first. Requires approval of the complete diff. "
                    "Rejects missing or ambiguous matches, stale contents, oversized files "
                    "or diffs, and unsupported file metadata. External access requires "
                    "separate read and write approval. Returns confirmation only after writing."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Existing file path, absolute or relative to the workspace."
                            ),
                        },
                        "old_text": {
                            "type": "string",
                            "description": (
                                "Nonempty exact text to replace. "
                                "Include enough context for one unique match."
                            ),
                        },
                        "new_text": {
                            "type": "string",
                            "description": (
                                "Replacement text. Must differ from old_text. "
                                "Empty text deletes the match."
                            ),
                        },
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        },
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
