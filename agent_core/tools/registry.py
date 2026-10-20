"""Pair each tool's Python function with its model-facing schema."""

from collections.abc import Callable

from agent_core.tools.files import (
    read_file_page,
    write_file,
    MAX_READ_CHARS,
    read_file_lines,
    MAX_READ_LINES
)
from agent_core.tools.shell import run_shell
from agent_core.tools.directories import list_directory
from agent_core.tools.search import search_files
from agent_core.tools.edits import edit_file
from agent_core.tools.discovery import find_files


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
    "read_file_lines": {
        "handler": read_file_lines,
        "schema": {
            "type": "function",
            "function": {
                "name": "read_file_lines",
                "description": (
                    "Read an inclusive, one-based range of lines from a UTF-8 file. "
                    f"At most {MAX_READ_LINES} lines and {MAX_READ_CHARS} content characters. "
                    "Returns JSON with path, start_line, actual end_line, lines, truncated, "
                    "and next_line. Each line has number and exact text with original endings. "
                    "truncated means more file content follows the returned range. "
                    "Oversized lines or ranges fail without returning partial lines; "
                    "use a smaller range or read_file character paging. "
                    "Each external or sensitive read requires approval."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path, absolute or relative to the workspace."
                            ),
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "First line to include. Line numbers start at 1."
                            ),
                        },
                        "end_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Last line to include, at least start_line. "
                                f"Request at most {MAX_READ_LINES} lines."
                            ),
                        },
                    },
                    "required": ["path", "start_line", "end_line"],
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
                    "Search literal, case-sensitive text recursively in UTF-8 files. "
                    "Match pattern against each basename; do not follow child symlinks. "
                    "Skips protected paths and generated directories; .gitignore is not applied. "
                    "Limits include depth, total entries, per-file bytes, matches, and skips. "
                    "Each external directory-tree search requires approval "
                    "for its root, query, and pattern. "
                    "Returns JSON with matches, skipped entries, and truncation flags. "
                    "Match paths are absolute; skipped names are relative to the search root. "
                    "Report skipped entries and truncation when describing coverage."
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
                                "Case-sensitive basename pattern such as *.py or *.txt. "
                                "Use * for all names. Use 1 to 100 characters "
                                "without path separators or line breaks."
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
    "find_files": {
        "handler": find_files,
        "schema": {
            "type": "function",
            "function": {
                "name": "find_files",
                "description": (
                    "Find filenames recursively without reading file contents. "
                    "Match a case-sensitive pattern against each basename. "
                    "Returns JSON with path, pattern, matches, skipped, and truncated. "
                    "Matches are sorted paths relative to the search root. "
                    "Skips child symlinks, protected paths, and generated directories. "
                    ".gitignore is not applied. Depth, scan, and result limits apply. "
                    "External roots require dedicated approval for each recursive filename search. "
                    "Access is denied when approval is unavailable. Protected roots are rejected. "
                    "Report skipped paths and truncation when describing coverage."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Search root, absolute or relative to the workspace. "
                                "Use '.' for the workspace. External roots require approval."
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Filename pattern such as *.py or *test*. "
                                "Use * for all names. Use 1 to 100 characters "
                                "without path separators or line breaks."
                            ),
                        },
                    },
                    "required": ["path", "pattern"],
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
