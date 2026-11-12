from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from agent_core.compaction import group_tool_exchanges
from agent_core.memory.session import load_session_state, save_session


INTERRUPTED_TOOL_RESULT = (
    "Oceanus recovery: no result was saved for this tool call. "
    "Its outcome is unknown; it may have changed files before interruption. "
    "Check the current state before deciding whether to repeat the action."
)

RECOVERY_NOTICE = (
    "This session contains tool calls with unknown outcomes. "
    "Check the current files before repeating actions. "
    "Opening the session does not execute tools."
)


def new_session_path() -> Path:
    """ Choose a session filename without creating an empty file """
    directory = Path.home() / ".local" / "share" / "oceanus" / "sessions"
    return directory / f"session-{uuid4().hex}.json"


def load_recovery_session(path: str | Path) -> tuple[list[dict], list[dict], Path | None]:
    """ Prepare both saved views without running tools or rewriting the file """
    history, active, workspace = load_session_state(path)
    recovered_history = prepare_recovery_messages(history)
    recovered_active = prepare_recovery_messages(active)
    return recovered_history, recovered_active, workspace


def has_interrupted_tools(messages: list[dict]) -> bool:
    """ Identify recovery markers without treating ordinary error text as a failure """
    for message in messages:
        if message.get("role") == "tool" and message.get("content") == INTERRUPTED_TOOL_RESULT:
            return True
    return False


def prepare_recovery_messages(messages: list[dict]) -> list[dict]:
    """ Copy history and mark missing results in its final tool batch """
    if not isinstance(messages, list):
        raise ValueError("messages must be a list!")

    recovered = deepcopy(messages)

    # Find the request before any results at the end of the history
    request_end = len(recovered)
    while request_end > 0:
        message = recovered[request_end - 1]
        if not isinstance(message, dict) or message.get("role") != "tool":
            break
        request_end -= 1

    if request_end > 0:
        request = recovered[request_end - 1]
        if isinstance(request, dict) and request.get("role") == "assistant":
            calls = request.get("tool_calls")
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise ValueError("tool_calls must be a list!")

            completed_ids = set()
            for result in recovered[request_end:]:
                call_id = result.get("tool_call_id")
                if not isinstance(call_id, str):
                    raise ValueError("Tool results need a string ID!")
                completed_ids.add(call_id)

            for call in calls:
                if not isinstance(call, dict):
                    raise ValueError("Each tool call must be a dictionary!")
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ValueError("Tool calls need a nonblank ID!")

                # Missing output can't tell whether the action ran
                if call_id not in completed_ids:
                    recovered.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": INTERRUPTED_TOOL_RESULT,
                    })

    # Reject malformed exchanges instead of inventing repairs elsewhere
    group_tool_exchanges(recovered)
    return recovered

class CheckpointError(RuntimeError):
    """ Report an expected failure while saving recovery state """


def save_recovery_checkpoint(
        path: str | Path,
        messages: list[dict],
        *,
        active_messages: list[dict],
        workspace: Path,
) -> None:
    """ Save recoverable copies without changing the running conversation """
    try:
        # Only saved copies receive markers for missing tool results
        history_snapshot = prepare_recovery_messages(messages)
        active_snapshot = prepare_recovery_messages(active_messages)

        checkpoint_path = Path(path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        # The existing writer replaces both views together
        save_session(
            checkpoint_path,
            history_snapshot,
            workspace=workspace,
            active_messages=active_snapshot
        )
    except (OSError, ValueError, TypeError) as error:
        raise CheckpointError(f"Couldn't save recovery checkpoint: {error}") from error
