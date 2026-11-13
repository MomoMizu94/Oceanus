# Call the model, execute requested tools, append results, and repeat until done
import json
from collections.abc import Callable
from pathlib import Path
from copy import deepcopy

from agent_core.model_client import (
    call_model, DEFAULT_MODEL, ModelResponseError, ModelOutputTruncatedError,
)
from agent_core.tools.registry import get_tool_function, get_tool_schemas, TOOL_REGISTRY
from agent_core.shell_sandbox import ShellSandboxError
from agent_core.compaction_runtime import compact_before_request
from agent_core.output_recovery import MAX_OUTPUT_RECOVERY_ATTEMPTS, build_recovery_request
from agent_core.memory.recovery import CheckpointError, save_recovery_checkpoint

DEFAULT_MAX_TURNS = 20

def run_agent(
        task: str,
        max_turns: int = DEFAULT_MAX_TURNS,
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
        on_edit_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
        on_discovery_approval: Callable[[str, str], bool] | None = None,
        on_web_approval: Callable[[str], bool] | None = None,
        on_fetch_approval: Callable[[str], bool] | None = None,
        *,
        attachments: list[dict] | None = None,
        reasoning: str | bool | None = None,
        active_messages: list[dict] | None = None,
        checkpoint_path: str | None = None,
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

    def request_edit_approval(path: str, diff: str) -> bool:
        if cancel_requested() or on_edit_approval is None:
            return False

        decision = on_edit_approval(path, diff)
        return decision is True and not cancel_requested()

    def request_sensitive_approval(opeation: str, path: str, reason: str) -> bool:
        if cancel_requested() or on_sensitive_approval is None:
            return False

        decision = on_sensitive_approval(opeation, path, reason)
        return decision is True and not cancel_requested()

    def request_discovery_approval(path: str, pattern: str) -> bool:
        if cancel_requested() or on_discovery_approval is None:
            return False

        decision = on_discovery_approval(path, pattern)
        if decision is not True:
            return False

        if cancel_requested():
            return False

        return True

    def request_web_approval(query: str) -> bool:
        # Don't open approval dialog for already cancelled task
        if cancel_requested() or on_web_approval is None:
            return False

        decision = on_web_approval(query)
        if decision is not True:
            return False

        # Cancellation can arrive while user is deciding
        if cancel_requested():
            return False

        return True

    def request_fetch_approval(url: str) -> bool:
        if cancel_requested() or on_fetch_approval is None:
            return False

        decision = on_fetch_approval(url)
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
    
    user_message = {"role": "user", "content": task}

    if attachments is not None:
        if not isinstance(attachments, list):
            raise ValueError("Attachments must be a list!")

        if attachments:
            # Keep submitted attachments separate from the editable draft
            user_message["attachments"] = deepcopy(attachments)

    # Get current workspace path
    workspace_root = Path.cwd().resolve()

    if conversation_history is None:
        conversation_history = []

    if active_messages is conversation_history:
        raise ValueError("Active context must be separate from the full transcript!")
    if active_messages is None:
        active_messages = deepcopy(conversation_history)
    if not isinstance(active_messages, list):
        raise ValueError("Active context must be a list!")
    if bool(active_messages) != bool(conversation_history):
        raise ValueError("Active context and full transcript must both be empty or nonempty!")

    checkpoint_error: str | None = None

    def write_checkpoint() -> None:
        nonlocal checkpoint_error
        if checkpoint_path is None or checkpoint_error is not None:
            return

        try:
            save_recovery_checkpoint(
                checkpoint_path,
                conversation_history,
                active_messages=active_messages,
                workspace=workspace_root,
            )
        except CheckpointError as error:
            # Stop this task after the first save failure
            checkpoint_error = (
                f"Stopped: {error}. No further tools will run in this task. "
                "Completed actions were not undone."
            )

    def append_message(message: dict) -> None:
        # Update both views before saving their recovery copies
        conversation_history.append(deepcopy(message))
        active_messages.append(deepcopy(message))
        write_checkpoint()

    # Use supplied instructions for fresh and resumed sessions
    if system_prompt:
        system_message = {
            "role": "system",
            "content": system_prompt
        }
        for history in (conversation_history, active_messages):
            if history and history[0].get("role") == "system":
                history[0] = deepcopy(system_message)
            else:
                history.insert(0, deepcopy(system_message))

    append_message(user_message)
    # Stops model contact if submitted prompt wasn't able to be saved
    if checkpoint_error is not None:
        return checkpoint_error

    compacted_this_task = False
    recovery_attempts = 0
    recovery_pending = False
    tool_schemas = get_tool_schemas(permitted_tools)

    for turn in range(1, max_turns + 1):
        report_progress(f"This is model turn: {turn}/{max_turns}")

        # Check for cancellation request
        if cancel_requested():
            return "Task cancelled."

        try:
            request_messages = active_messages
            if recovery_pending:
                request_messages = build_recovery_request(active_messages)
            # Every previous tool batch is complete before reaching this boundary
            compacted = compact_before_request(
                request_messages, tool_schemas, model=model, reasoning=reasoning,
                already_compacted=compacted_this_task,
                should_cancel=cancel_requested, on_progress=report_progress,
            )
            compacted_this_task = compacted_this_task or compacted
            if compacted and recovery_pending:
                # Save the summary, but remove the retry-only instruction first
                retained_messages = deepcopy(request_messages)
                if active_messages and active_messages[0].get("role") == "system":
                    retained_messages[0] = deepcopy(active_messages[0])
                else:
                    retained_messages.pop(0)
                active_messages[:] = retained_messages
            # Compaction's own checkpoint
            if compacted:
                write_checkpoint()
                if checkpoint_error is not None:
                    return checkpoint_error
            if cancel_requested():
                return "Task cancelled."
            reply = call_model(
                request_messages,
                model=model,
                allowed_tools=permitted_tools,
                reasoning=reasoning,
            )
        except InterruptedError:
            return "Task cancelled."
        except ModelOutputTruncatedError as error:
            if cancel_requested():
                return "Task cancelled."
            if recovery_attempts >= MAX_OUTPUT_RECOVERY_ATTEMPTS:
                return (
                    str(error) + " Automatic output recovery was already used for this task; "
                    "no further retry was sent."
                )
            if turn >= max_turns:
                return (
                    str(error) + " No recovery request was sent because "
                    "the task's turn limit was reached."
                )

            recovery_attempts += 1
            recovery_pending = True
            report_progress(
                "Output limit reached. Retrying once with guidance for a smaller, complete response. "
                "The retry uses the next model turn; reasoning and output limits are unchanged."
            )
            continue
        # For token limits and failed compaction
        except ModelResponseError as error:
            if cancel_requested():
                return "Task cancelled."
            return str(error)

        # Check for cancellation request
        if cancel_requested():
            return "Task cancelled."

        # Don't save tool requests that will not be executed
        if reply.tool_calls and turn == max_turns:
            break

        if recovery_pending:
            report_progress("Output-limit recovery returned a complete response.")
            recovery_pending = False

        # Preserve agent's tool requests before adding to result
        append_message(reply.model_dump(exclude_none=True))

        # If no tool requests -> task finished (+ save failure)
        if not reply.tool_calls:
            if checkpoint_error is not None:
                return checkpoint_error
            return reply.content or ""

        # Show explanations regarding the requested tools
        if reply.content:
            report_progress(f"Agent: {reply.content}")

        for tool_call in reply.tool_calls:
            # Translate requests into function calls
            tool_name = tool_call.function.name
            is_file_tool = tool_name in ("read_file",
                                         "read_file_lines",
                                         "write_file",
                                         "list_directory",
                                         "search_files",
                                         "edit_file",
                                         "export_pdf",
                                         "find_files",)
            # Web content can contain errors
            # Keep operation's status separate from content returned by search
            uses_structured_result = is_file_tool or tool_name in ("web_search", "fetch_page")
            file_result = None

            if checkpoint_error is not None or cancel_requested():
                if checkpoint_error is not None:
                    result = "Tool skipped: recovery checkpoint failed before execution."
                else:
                    result = "Tool skipped: task cancelled before execution."

                if uses_structured_result:
                    file_result = {
                        "status": "skipped",
                        "message": result,
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
                    elif tool_name == "web_search":
                        # Supply Oceanus' callback
                        result = tool_function(**arguments, on_web_approval=request_web_approval)
                    elif tool_name == "fetch_page":
                        # Only Oceanus supplies approval and cancellation callbacks
                        result = tool_function(
                            **arguments,
                            on_fetch_approval=request_fetch_approval,
                            should_cancel=cancel_requested,
                        )
                    elif tool_name == "find_files":
                        result = tool_function(**arguments, workspace_root=workspace_root, on_discovery_approval=request_discovery_approval)
                    elif tool_name == "search_files":
                        result = tool_function(**arguments, workspace_root=workspace_root, on_search_approval=request_search_approval)
                    elif tool_name == "export_pdf":
                        result = tool_function(
                            **arguments,
                            workspace_root=workspace_root,
                            on_file_approval=request_file_approval,
                            on_sensitive_approval=request_sensitive_approval,
                            should_cancel=cancel_requested,
                        )
                    elif tool_name == "edit_file":
                        result = tool_function(
                            **arguments,
                            workspace_root=workspace_root,
                            on_file_approval=request_file_approval,
                            on_edit_approval=request_edit_approval,
                            should_cancel=cancel_requested,
                            on_sensitive_approval=request_sensitive_approval,
                        )
                    elif is_file_tool:
                        result = tool_function(
                            **arguments,
                            workspace_root=workspace_root,
                            on_file_approval=request_file_approval,
                            on_sensitive_approval=request_sensitive_approval
                        )
                    else:
                        result = tool_function(**arguments)

                except (ValueError, KeyError, TypeError, OSError, ShellSandboxError) as error:
                    # Report failed tool execution so model can respond
                    result = f"Tool error: {type(error).__name__}: {error}"
                    if uses_structured_result:
                        file_result = {
                            "status": "error",
                            "error_type": type(error).__name__,
                            "message": str(error)
                        }

                else:
                    # File operation marked as successful
                    if uses_structured_result:
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
            append_message(
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

        if checkpoint_error is not None:
            return checkpoint_error

        if cancel_requested():
            return "Task cancelled."

        if cancel_requested():
            return "Task cancelled."

    return (
          f"Stopped after {max_turns} was hit: the model still required more tool calls."
      )
