from litellm import completion
import os
import json

from agent_core.tools.registry import get_tool_schemas


DEFAULT_MODEL = "openai/gpt-4.1"
DEFAULT_MAX_COMPLETION_TOKENS = 4096


class ModelResponseError(RuntimeError):
    """ A model response cannot safely be used by agent """


class ModelOutputTruncatedError(ModelResponseError):
    """ Provider reported an incomplete response at its output limit """


def get_max_completion_tokens() -> int:
    """ Read and validate output-token limit for one model request """
    raw_value = os.environ.get(
        "OCEANUS_MAX_COMPLETION_TOKENS",
        str(DEFAULT_MAX_COMPLETION_TOKENS)
    )
    error_message = "OCEANUS_MAX_COMPLETION_TOKENS must be a positive integer."

    try:
        limit = int(raw_value)
    except ValueError:
        raise ValueError(error_message) from None

    if limit < 1:
        raise ValueError(error_message)

    return limit


def call_completion(
        conversation_history, 
        model: str = DEFAULT_MODEL, 
        *, 
        use_tools: bool = True, 
        allowed_tools: set[str] | frozenset[str] | None = None
):
    """ Make one model request and return full response """
    """ Preserve Ollama's native stop reason """
    tools = get_tool_schemas(allowed_tools) if use_tools else []
    is_ollama = model.startswith("ollama_chat/")
    native_reason = None

    def capture_ollama_status(details):
        """ Called by LiteLLM before and after its HTTP request """
        nonlocal native_reason
        event = details.get("log_event_type")
        if event == "pre_api_call":
            native_reason = None
            return
        if event != "post_api_call":
            return

        native_reason = None
        raw = details.get("original_response")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return

        if isinstance(raw, dict) and raw.get("done") is True:
            reason = raw.get("done_reason")
            if reason in ("stop", "length"):
                native_reason = reason

    extra_options = {"logger_fn": capture_ollama_status} if is_ollama else {}
    response = completion(
        model=model,
        messages=conversation_history,
        tools=tools or None,
        max_completion_tokens=get_max_completion_tokens(),
        stream=False,
        **extra_options
    )

    if is_ollama:
        if native_reason is None:
            raise ModelResponseError(
                "Stopped: could not verify Ollama's completion status. "
                "The response was discarded; no tools from it were executed."
            )
        if native_reason == "length":
            response.choices[0].finish_reason = "length"

    return response


def call_model(conversation_history, model: str = DEFAULT_MODEL, *, allowed_tools: set[str] | frozenset[str] | None = None):
    """ Return assistant message for agent loop """
    response = call_completion(conversation_history, model=model, allowed_tools=allowed_tools)
    choice = response.choices[0]

    if choice.finish_reason == "length":
        raise ModelOutputTruncatedError(
            "Stopped: the model reached its output limit. "
            "The incomplete response was discarded; no tools from that response were executed. "
            "Earlier tool actions, if any, were not undone. "
            "Increase OCEANUS_MAX_COMPLETION_TOKENS or request a smaller change before trying again."
        )

    return choice.message
