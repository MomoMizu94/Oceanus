from collections.abc import Callable


from agent_core import model_client
from agent_core.compaction import (
    MAX_COMPACTION_SUMMARY_CHARS,
    build_compaction_request,
    build_compacted_context,
    estimate_request_tokens,
    select_compaction_messages,
    should_compact,
)


COMPACTION_OUTPUT_TOKENS = 4096
COMPACTION_TIMEOUT_SECONDS = 180
# Reserve Qwen's standard maximum image budget plus room for image markers
QWEN_IMAGE_TOKEN_RESERVE = 16_896


def compact_before_request(
    active_messages: list[dict],
    tools: list[dict],
    *,
    model: str,
    reasoning: str | bool | None = None,
    already_compacted: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> bool:
    """ Replace active context only after a complete, validated summary """
    context_tokens = model_client.get_context_tokens(model)
    if context_tokens is None:
        return False

    image_reserve = None
    for message in active_messages:
        if message.get("attachments"):
            if model != model_client.QWEN_Q4_MODEL:
                # Other vision models need their own accounting policy
                return False
            image_reserve = QWEN_IMAGE_TOKEN_RESERVE
            break

    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Compaction cancelled!")

    try:
        check_cancelled()
        output_tokens = model_client.get_max_completion_tokens(model)
        input_tokens = estimate_request_tokens(
            active_messages, tools, model=model, image_tokens_per_image=image_reserve,
        )
        needs_compaction = should_compact(input_tokens, context_tokens, output_tokens)
        try:
            model_client.check_request_budget(active_messages, tools)
        except model_client.RequestBudgetExceededError:
            needs_compaction = True
        if not needs_compaction:
            return False
        if already_compacted:
            raise ValueError("The context filled again after this task's one compaction attempt")

        selection = select_compaction_messages(active_messages)
        if not selection["to_summarize"]:
            raise ValueError("Only protected or recent messages remain; there is nothing to summarize")
        current_task = ""
        for message in reversed(active_messages):
            if message.get("role") == "user":
                current_task = message.get("content") or ""
                break

        if on_progress is not None:
            on_progress("Compacting conversation: summarizing older exchanges...")
        summary = generate_compaction_summary(
            selection["to_summarize"], current_task=current_task, model=model,
            reasoning=reasoning, should_cancel=should_cancel,
        )
        check_cancelled()
        candidate = build_compacted_context(
            active_messages, summary, tools, model=model,
            context_tokens=context_tokens, output_tokens=output_tokens,
            image_tokens_per_image=image_reserve,
        )
        model_client.check_request_budget(candidate, tools)
        check_cancelled()
    except InterruptedError:
        raise
    except Exception as error:
        # Provider, parsing, and budget failures must leave active history intact
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Compaction cancelled!") from error
        detail = str(error).rstrip(". ")
        raise model_client.ModelResponseError(
            f"Stopped: automatic compaction could not prepare the next request. {detail}. "
            "The existing history was preserved. Start a shorter conversation or adjust the limits."
        ) from error

    previous_count = len(active_messages)
    active_messages[:] = candidate
    if on_progress is not None:
        on_progress(
            f"Conversation compacted: {previous_count} active messages reduced to {len(candidate)}. "
            "The full transcript is preserved."
        )
    return True


def generate_compaction_summary(
        to_summarize: list[dict],
        *,
        current_task: str,
        model: str,
        reasoning: str | bool | None = None,
        should_cancel: Callable[[], bool] | None = None,
) -> str:
    """ Generate a checked summary without modifying the conversation """
    def check_cancelled():
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Compaction cancelled!")

    check_cancelled()
    context_tokens = model_client.get_context_tokens(model)
    if context_tokens is None:
        raise model_client.ModelResponseError("Compaction requires a known model context allocation!")

    request = build_compaction_request(
        to_summarize,
        current_task=current_task,
        model=model,
        context_tokens=context_tokens,
        output_tokens=COMPACTION_OUTPUT_TOKENS,
    )
    check_cancelled()

    # Summary generation is not allowed to execute tools
    response = model_client.call_completion(
        request,
        model=model,
        use_tools=False,
        reasoning=reasoning,
        output_tokens=COMPACTION_OUTPUT_TOKENS,
        timeout_seconds=COMPACTION_TIMEOUT_SECONDS,
        allow_retries=False
    )
    check_cancelled()

    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise model_client.ModelResponseError("Extected one compaction summary!")

    choice = choices[0]
    if getattr(choice, "finish_reason", None) != "stop":
        raise model_client.ModelResponseError("The compaction summary was not completed!")

    message = getattr(choice, "message", None)
    if getattr(message, "role", None) != "assistant":
        raise model_client.ModelResponseError("Invalid compaction summary message!")
    if getattr(message, "tool_calls", None) or getattr(message, "function_call", None):
        raise model_client.ModelResponseError("Compaction returned an unexpected tool request!")
    if getattr(message, "refusal", None):
        raise model_client.ModelResponseError("The model refused to summarize the history!")

    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise model_client.ModelResponseError("The compaction summary is empty!")

    # Reject oversized summaries
    if len(content) > MAX_COMPACTION_SUMMARY_CHARS:
        raise model_client.ModelResponseError("The compaction summary exceeded its character limit!")

    return content
