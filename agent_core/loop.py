# Call the model, execute requested tools, append results, and repeat until done
import json
from collections.abc import Callable
from pathlib import Path

from agent_core.model_client import call_model, DEFAULT_MODEL
from agent_core.tools.registry import get_tool_function, TOOL_REGISTRY

def run_agent(
        task: str,
        max_turns: int = 10,
        system_prompt: str = "",
        model: str = DEFAULT_MODEL,
        conversation_history: list[dict] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_approval: Callable[[str, str], bool] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_file_approval: Callable[[str, str], bool] | None = None,
        allowed_tools: set[str] | None = None,
        on_tool_status: Callable[[str, str], None] | None = None,
        on_search_approval: Callable[[str, str, str], bool] | None = None,
        ) -> str:
    """ Run a task until model answers or hits limit """

    def report_progress(message: str) -> None:
        """ Helper function for callback """
        if on_progress is not None:
            on_progress(message)

    def cancel_requested() -> bool:
        """ Helper function for task cancellation """
        if should_cancel is None:
            return False

        return should_cancel()

    def request_file_approval(operation: str, path: str) -> bool:
        """ Helper function for approval requests. """
        if cancel_requested() or on_file_approval is None:
            return False

        decision = on_file_approval(operation, path)
        return decision is True and not cancel_requested()

    def request_search_approval(path: str, query: str, pattern: str) -> bool:
        """ Helper for seach approvals """
        if cancel_requested() or on_search_approval is None:
            return False

        decision = on_search_approval(path, query, pattern)
        return decision is True and not cancel_requested()

    if max_turns < 1:
        raise ValueError ("max_turns must be at least 1")

    # Tool selection for a task
    registered_tools = set(TOOL_REGISTRY)
    # Fixed copy for a run
    permitted_tools = frozenset(registered_tools if allowed_tools is None else allowed_tools)

    unknown_tools = permitted_tools - registered_tools
    if unknown_tools:
        raise ValueError(f"Unknown allowed tools: {', '.join(sorted(unknown_tools))}")

    # Get current workspace path
    workspace_root = Path.cwd().resolve()

    if conversation_history is None:
        conversation_history = []

    # Use supplied instructions for fresh and resumed sessions
    if system_prompt:
        system_message = {
            "role": "system",
            "content": system_prompt
        }
        if conversation_history and conversation_history[0].get("role") == "system":
            conversation_history[0] = system_message
        else:
            conversation_history.insert(0, system_message)

    conversation_history.append(
        {
            "role": "user",
            "content": task
        }
    )

    for turn in range(1, max_turns + 1):
        report_progress(f"This is model turn: {turn}/{max_turns}")

        # Check for cancellation request
        if cancel_requested():
            return "Task cancelled."

        reply = call_model(conversation_history, model=model, allowed_tools=permitted_tools)

        # Check for cancellation request
        if cancel_requested():
            return "Task cancelled."

        # Don't save tool requests that will not be executed
        if reply.tool_calls and turn == max_turns:
            break

        # Preserve agent's tool requests before adding to result
        conversation_history.append(reply.model_dump(exclude_none=True))

        # If no tool requests -> task finished
        if not reply.tool_calls:
            return reply.content or ""

        # Show explanations regarding the requested tools
        if reply.content:
            report_progress(f"Agent: {reply.content}")

        for tool_call in reply.tool_calls:
            # Translate requests into function calls
            tool_name = tool_call.function.name
            is_file_tool = tool_name in ("read_file",
                                         "write_file",
                                         "list_directory",
                                         "search_files")
            file_result = None

            if cancel_requested():
                result = "Tool skipped: task cancelled before execution."
                if is_file_tool:
                    file_result = {
                        "status": "skipped",
                        "message": result
                    }
            else:
                try:
                    if tool_name not in permitted_tools:
                        raise PermissionError(f"Tool is not allowed for this run: {tool_name}")

                    report_progress(f"Running tool: {tool_name}")
                    arguments = json.loads(tool_call.function.arguments)
                    tool_function = get_tool_function(tool_name)
                    
                    if tool_name == "run_shell":
                        result = tool_function(**arguments, on_approval=on_approval)
                    elif tool_name == "search_files":
                        result = tool_function(**arguments, workspace_root=workspace_root, on_search_approval=request_search_approval)
                    elif is_file_tool:
                        result = tool_function(**arguments, workspace_root=workspace_root, on_file_approval=request_file_approval)
                    else:
                        result = tool_function(**arguments)

                except (ValueError, KeyError, TypeError, OSError) as error:
                    # Report failed tool execution so model can respond
                    result = f"Tool error: {type(error).__name__}: {error}"
                    if is_file_tool:
                        file_result = {
                            "status": "error",
                            "error_type": type(error).__name__,
                            "message": str(error)
                        }

                else:
                    # File operation marked as successful
                    if is_file_tool:
                        file_result = {
                            "status": "success",
                            "content": result
                        }

            model_content = result
            if file_result is not None:
                model_content = json.dumps(
                    {"tool": tool_name, **file_result},
                    ensure_ascii=False
                )

            # Record results
            conversation_history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": model_content
                }
            )

            if on_tool_status is not None and file_result is not None:
                on_tool_status(tool_name, file_result["status"])
            
            if on_tool_result is not None:
                on_tool_result(tool_name, result)

        if cancel_requested():
            return "Task cancelled."

    return (
          f"Stopped after {max_turns} was hit: the model still required more tool calls."
      )