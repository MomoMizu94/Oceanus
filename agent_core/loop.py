# Call the model, execute requested tools, append results, and repeat until done
import json
from collections import deque
from collections.abc import Callable
from pathlib import Path
from copy import deepcopy

from agent_core.model_client import (
    call_model, DEFAULT_MODEL, QWEN_Q4_MODEL, ModelResponseError, ModelOutputTruncatedError,
)
from agent_core.tools.registry import get_tool_function, get_tool_schemas, TOOL_REGISTRY
from agent_core.tools.reddit import RedditLookupScope
from agent_core.shell_sandbox import ShellSandboxError
from agent_core.compaction_runtime import compact_before_request
from agent_core.turn_budget import build_turn_budget_request
from agent_core.tool_progress import describe_tool_outcome
from agent_core.output_recovery import (
    MAX_OUTPUT_RECOVERY_ATTEMPTS, RECOVERY_FINAL_GUIDANCE, RECOVERY_STEP_GUIDANCE,
    build_recovery_request, qwen_recovery_context,
)
from agent_core.memory.recovery import CheckpointError, save_recovery_checkpoint
from agent_core.permissions import PermissionMode, automatically_approve
from agent_core.local_image_runtime import ImageCleanupError

DEFAULT_MAX_TURNS = 30
PROGRESS_AFTER_TOOL_CALLS = 3

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
        on_render_approval: Callable[[str], bool] | None = None,
        on_pdf_fetch_approval: Callable[[str], bool] | None = None,
        attachments: list[dict] | None = None,
        reasoning: str | bool | None = None,
        active_messages: list[dict] | None = None,
        checkpoint_path: str | None = None,
        permission_mode: PermissionMode | str = PermissionMode.ASK,
        followups_waiting: Callable[[], bool] | None = None,
        ) -> str | None:
    """Run a task. Return None for a requested handoff after a complete tool batch."""

    permission_mode = PermissionMode(permission_mode)

    def decide(
        action: str, callback: Callable[..., bool] | None, *arguments,
        path: str = "", command: str = "",
    ) -> bool:
        if cancel_requested():
            return False
        if action == "web":
            reddit_scope.check_web_request(arguments[0])
        auto_approved = automatically_approve(
            permission_mode, action, workspace_root, path=path, command=command,
        )
        if auto_approved:
            return not cancel_requested()
        if callback is None:
            return False
        decision = callback(*arguments)
        return decision is True and not cancel_requested()

    def request_shell_approval(command: str, working_directory: str) -> bool:
        return decide("shell", on_approval, command, working_directory, command=command)

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
        return decide("file", on_file_approval, operation, path)

    def request_search_approval(path: str, query: str, pattern: str) -> bool:
        return decide("file", on_search_approval, path, query, pattern)

    def request_edit_approval(path: str, diff: str) -> bool:
        return decide("edit", on_edit_approval, path, diff, path=path)

    def request_sensitive_approval(operation: str, path: str, reason: str) -> bool:
        return decide("sensitive", on_sensitive_approval, operation, path, reason)

    def request_discovery_approval(path: str, pattern: str) -> bool:
        return decide("file", on_discovery_approval, path, pattern)

    def request_web_approval(query: str) -> bool:
        return decide("web", on_web_approval, query)

    def request_pdf_fetch_approval(url: str) -> bool:
        return decide("web", on_pdf_fetch_approval, url)

    def request_render_approval(url: str) -> bool:
        return decide("web", on_render_approval, url)

    def request_fetch_approval(url: str) -> bool:
        return decide("web", on_fetch_approval, url)

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

    # Queued user messages can name several boards for the same task
    current_prompts = []
    for message in reversed(conversation_history):
        if message.get("role") != "user":
            break
        current_prompts.append(message["content"])
    reddit_scope = RedditLookupScope(current_prompts)

    compacted_this_task = False
    recovery_attempts = 0
    recovery_pending = False
    recovering_qwen = False
    request_reasoning = reasoning
    recent_outcomes: deque[str] = deque(maxlen=PROGRESS_AFTER_TOOL_CALLS)
    tool_schemas = get_tool_schemas(permitted_tools)
    first_edit_turn = None

    for turn in range(1, max_turns + 1):
        report_progress(f"This is model turn: {turn}/{max_turns}")

        # Check for cancellation request
        if cancel_requested():
            return "Task cancelled."

        if followups_waiting is not None and followups_waiting():
            return None

        try:
            progress_due = len(recent_outcomes) == PROGRESS_AFTER_TOOL_CALLS
            context = active_messages
            if recovering_qwen:
                context = qwen_recovery_context(active_messages)
            request_messages = build_turn_budget_request(
                context, turn, max_turns, progress_due=progress_due,
                first_edit_done=first_edit_turn == turn - 1,
            )
            request_tools = permitted_tools
            request_schemas = tool_schemas
            final_recovery_turn = recovering_qwen and turn == max_turns
            if final_recovery_turn:
                request_tools = set()
                request_schemas = []
                # Count the notice without replacing the user task protected by compaction
                request_messages.append({"role": "system", "content": RECOVERY_FINAL_GUIDANCE})
            elif recovery_pending:
                request_messages = build_recovery_request(request_messages)
            elif recovering_qwen:
                request_messages = build_recovery_request(request_messages, guidance=RECOVERY_STEP_GUIDANCE)
            # Every previous tool batch is complete before reaching this boundary
            compacted = compact_before_request(
                request_messages, request_schemas, model=model, reasoning=request_reasoning,
                already_compacted=compacted_this_task,
                should_cancel=cancel_requested, on_progress=report_progress,
            )
            compacted_this_task = compacted_this_task or compacted
            if compacted and request_messages is not active_messages:
                # Save the summary without temporary recovery or turn-budget instructions
                retained_messages = deepcopy(request_messages)
                if active_messages and active_messages[0].get("role") == "system":
                    retained_messages[0] = deepcopy(active_messages[0])
                elif retained_messages[0].get("role") == "system":
                    retained_messages.pop(0)
                if final_recovery_turn:
                    retained_messages.pop()
                active_messages[:] = retained_messages
            # Compaction's own checkpoint
            if compacted:
                write_checkpoint()
                if checkpoint_error is not None:
                    return checkpoint_error
            if cancel_requested():
                return "Task cancelled."
            if followups_waiting is not None and followups_waiting():
                return None
            if final_recovery_turn:
                # Qwen treats a trailing user-role message as a request to answer the tool results
                request_messages[-1] = {"role": "user", "content": RECOVERY_FINAL_GUIDANCE}
            reply = call_model(
                request_messages,
                model=model,
                allowed_tools=request_tools,
                reasoning=request_reasoning,
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
            retry_notice = "The retry uses the next model turn."
            recovering_qwen = model == QWEN_Q4_MODEL
            if recovering_qwen and reasoning is None:
                request_reasoning = False
                retry_notice += " Turning off model thinking for the rest of this task."
            report_progress(
                "Output limit reached. Retrying once with guidance for a smaller, complete response. "
                + retry_notice
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
        if reply.content and reply.content.strip():
            report_progress(f"Agent: {reply.content}")
            recent_outcomes.clear()

        image_cleanup_error = None
        for tool_call in reply.tool_calls:
            # Translate requests into function calls
            tool_name = tool_call.function.name
            display_name = tool_name if tool_name in registered_tools else "unknown tool"
            if len(recent_outcomes) == PROGRESS_AFTER_TOOL_CALLS:
                summary = ", ".join(recent_outcomes)
                report_progress(f"Progress: {summary}. Next request: {display_name}.")
                recent_outcomes.clear()
            arguments = None
            is_file_tool = tool_name in ("read_file",
                                         "profile_csv",
                                         "lookup_csv_records",
                                         "generate_image",
                                         "analyze_csv",
                                         "export_csv_report",
                                         "export_csv_chart",
                                         "read_pdf",
                                         "read_file_lines",
                                         "write_file",
                                         "list_directory",
                                         "search_files",
                                         "edit_file",
                                         "export_pdf",
                                         "export_canvas",
                                         "find_files",)
            # Web content can contain errors
            # Keep operation's status separate from content returned by search
            uses_structured_result = is_file_tool or tool_name in ("web_search", "search_reddit", "fetch_page", "fetch_pdf", "render_page")
            file_result = None

            if checkpoint_error is not None or image_cleanup_error is not None or cancel_requested():
                if checkpoint_error is not None:
                    result = "Tool skipped: recovery checkpoint failed before execution."
                elif image_cleanup_error is not None:
                    result = "Tool skipped: local image cleanup needs attention."
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
                        result = tool_function(
                            **arguments, on_approval=request_shell_approval,
                            full_access=permission_mode == PermissionMode.FULL,
                        )
                    elif tool_name == "search_reddit":
                        result = tool_function(**arguments, on_web_approval=request_web_approval,
                                               lookup_scope=reddit_scope)
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
                    elif tool_name == "render_page":
                        result = tool_function(
                            **arguments, on_fetch_approval=request_render_approval,
                            should_cancel=cancel_requested,
                        )
                    elif tool_name == "fetch_pdf":
                        result = tool_function(
                            **arguments, on_fetch_approval=request_pdf_fetch_approval,
                            should_cancel=cancel_requested,
                        )
                    elif tool_name in ("read_pdf", "profile_csv", "lookup_csv_records"):
                        result = tool_function(
                            **arguments, workspace_root=workspace_root,
                            on_file_approval=request_file_approval,
                            on_sensitive_approval=request_sensitive_approval,
                            should_cancel=cancel_requested,
                        )
                    elif tool_name == "find_files":
                        result = tool_function(**arguments, workspace_root=workspace_root, on_discovery_approval=request_discovery_approval)
                    elif tool_name == "search_files":
                        result = tool_function(**arguments, workspace_root=workspace_root, on_search_approval=request_search_approval)
                    elif tool_name == "generate_image":
                        result = tool_function(
                            **arguments, workspace_root=workspace_root, active_model=model,
                            on_file_approval=request_file_approval,
                            on_sensitive_approval=request_sensitive_approval,
                            should_cancel=cancel_requested, on_progress=report_progress,
                            require_write_approval=permission_mode == PermissionMode.READ_ONLY,
                        )
                    elif tool_name in ("export_pdf", "export_canvas", "analyze_csv", "export_csv_report", "export_csv_chart"):
                        result = tool_function(
                            **arguments,
                            require_write_approval=permission_mode == PermissionMode.READ_ONLY,
                            workspace_root=workspace_root,
                            on_file_approval=request_file_approval,
                            on_sensitive_approval=request_sensitive_approval,
                            should_cancel=cancel_requested,
                        )
                    elif tool_name == "write_file":
                        result = tool_function(
                            **arguments, workspace_root=workspace_root,
                            on_file_approval=request_file_approval,
                            on_sensitive_approval=request_sensitive_approval,
                            require_write_approval=permission_mode == PermissionMode.READ_ONLY,
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
                    if isinstance(error, ImageCleanupError):
                        image_cleanup_error = str(error)
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

            if (first_edit_turn is None and tool_name in ("edit_file", "write_file")
                    and file_result is not None and file_result["status"] == "success"):
                first_edit_turn = turn

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
            if on_progress is not None:
                recent_outcomes.append(describe_tool_outcome(display_name, arguments, file_result))
            
            if on_tool_result is not None:
                on_tool_result(tool_name, result)

        if image_cleanup_error is not None:
            return "Stopped: " + image_cleanup_error

        if checkpoint_error is not None:
            return checkpoint_error

        if cancel_requested():
            return "Task cancelled."
        # A pending correction can take over before another model turn or compaction
        if followups_waiting is not None and followups_waiting():
            return None

    return (
          f"Stopped after {max_turns} model turns: the model still requested tools. "
          "Tool requests from the final response were not executed. Earlier completed work "
          "is preserved; continue with the remaining deliverable rather than restarting the analysis."
      )
