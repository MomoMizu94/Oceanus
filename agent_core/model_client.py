from litellm import completion, supports_vision
import os
import json
import re
from copy import deepcopy
from pathlib import Path
from http.client import HTTPException
import math

from agent_core.tools.registry import get_tool_schemas
from agent_core.image_attachments import build_user_content, MAX_IMAGES_PER_MESSAGE
from agent_core.model_discovery import ollama_supports_images


DEFAULT_MODEL = "openai/gpt-4.1"
QWEN_Q4_MODEL = "ollama_chat/qwen3.8:27b-q4_K_M"
DEFAULT_MAX_COMPLETION_TOKENS = 4096
# Q4 needs room for reasoning and generated code in the same response
QWEN_Q4_MAX_COMPLETION_TOKENS = 16_384
QWEN_Q4_CONTEXT_TOKENS = 98_304
# Limits for the entire conversation sent in one request
MAX_REQUEST_IMAGES = 8
MAX_REQUEST_IMAGE_CHARS = 48 * 1024 * 1024


class ModelResponseError(RuntimeError):
    """ A model response cannot safely be used by agent """


class ModelOutputTruncatedError(ModelResponseError):
    """ Provider reported an incomplete response at its output limit """


class ModelContextLimitError(ModelResponseError):
    """ The conversation and generated tokens filled the context window """


DEFAULT_MAX_REQUEST_CHARS = 512_000


class RequestBudgetExceededError(ModelResponseError):
    """ Next request exceeds Oceanus' configured input-size budget """


def default_reasoning(model: str) -> str | None:
    if model == QWEN_Q4_MODEL:
        return "medium"
    return None


def get_max_request_chars() -> int:
    raw_value = os.environ.get("OCEANUS_MAX_REQUEST_CHARS", str(DEFAULT_MAX_REQUEST_CHARS))
    error_message = "OCEANUS_MAX_REQUEST_CHARS must be a positive integer"

    try:
        limit = int(raw_value)
    except ValueError:
        raise ValueError(error_message) from None

    if limit < 1:
        raise ValueError(error_message)
    return limit

def check_request_budget(conversation_history, tools) -> None:
    """ Count serialized input characters without trimming history """
    limit = get_max_request_chars()
    payload = {
        "messages": conversation_history,
        "tools": tools or None
    }
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    size = sum(len(chunk) for chunk in encoder.iterencode(payload))

    if size > limit:
        raise RequestBudgetExceededError(
            f"Stopped: request input contains {size:,} serialized characters, "
            f"exceeding OCEANUS_MAX_REQUEST_CHARS={limit:,}. "
            "The next model request was not sent. History was not trimmed. "
            "Save this session and start a new one, or raise the input-size budget. "
            "The model's own context limit still applies. "
            "Earlier tool actions, if any, were not undone."
        )

def prepare_model_messages(conversation_history: list[dict], tools, *, attachment_dir: Path | None = None) -> list[dict]:
    """ Build request messages without changing saved conversation data """
    if not isinstance(conversation_history, list):
        raise ModelResponseError("Conversation history must be a list!")

    # Count text, tool schemas and attachment metadata before loading images
    check_request_budget(conversation_history, tools)
    messages = deepcopy(conversation_history)
    image_count = 0

    for message in messages:
        if not isinstance(message, dict):
            raise ModelResponseError("Each message must be a dictionary!")

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelResponseError("Saves message content must be text or null!")
        if "attachments" not in message:
            continue

        if message.get("role") != "user" or not isinstance(content, str):
            raise ModelResponseError("Attachments requires a user message with text content!")

        attachments = message["attachments"]
        if not isinstance(attachments, list):
            raise ModelResponseError("Attachments must be a list!")
        if len(attachments) > MAX_IMAGES_PER_MESSAGE:
            raise RequestBudgetExceededError("Too many images in one message!")

        labels = set()
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ModelResponseError("Each attachment must be a dictionary!")
            if not isinstance(attachment.get("id"), str):
                raise ModelResponseError("Each attachment needs a string ID!")
            if "label" in attachment:
                label = attachment["label"]
                if not isinstance(label, str) or len(label) > 32:
                    raise ModelResponseError("Invalid image attachment label!")
                if re.fullmatch(r"\[Image [1-9][0-9]*\]", label) is None:
                    raise ModelResponseError("Invalid image attachment label!")
                if label in labels:
                    raise ModelResponseError("Image labels must be unique within a message!")
                labels.add(label)

        image_count += len(attachments)
        if image_count > MAX_REQUEST_IMAGES:
            raise RequestBudgetExceededError("Too many images across the conversation!")

    # Only expand images after checking every message and the total image count
    image_chars = 0
    for message in messages:
        attachments = message.pop("attachments", [])
        if not attachments:
            continue

        attachment_ids = []
        for attachment in attachments:
            attachment_ids.append(attachment["id"])

        try:
            content = build_user_content(message["content"], attachment_ids, attachment_dir=attachment_dir)
        except (OSError, ValueError) as error:
            raise ModelResponseError(f"Could not prepare image attachments: {error}") from error

        labeled_content = []
        image_index = 0
        for part in content:
            if part["type"] == "image_url":
                # Keep each image tied to its marker after reordering or deletion
                label = attachments[image_index].get("label")
                if label is not None:
                    labeled_content.append({"type": "text", "text": label})
                image_index += 1
                image_chars += len(part["image_url"]["url"])
            labeled_content.append(part)

        if image_chars > MAX_REQUEST_IMAGE_CHARS:
            raise RequestBudgetExceededError("Encoded images exceed the request image budget!")

        message["content"] = labeled_content

    return messages

def get_max_completion_tokens(model: str = DEFAULT_MODEL) -> int:
    """ Read and validate output-token limit for one model request """
    default_limit = DEFAULT_MAX_COMPLETION_TOKENS
    if model == QWEN_Q4_MODEL:
        default_limit = QWEN_Q4_MAX_COMPLETION_TOKENS
    raw_value = os.environ.get(
        "OCEANUS_MAX_COMPLETION_TOKENS",
        str(default_limit)
    )
    error_message = "OCEANUS_MAX_COMPLETION_TOKENS must be a positive integer."

    try:
        limit = int(raw_value)
    except ValueError:
        raise ValueError(error_message) from None

    if limit < 1:
        raise ValueError(error_message)

    return limit

def get_context_tokens(model: str) -> int | None:
    """ Choose Ollama's context allocation, leaving cloud settings alone """
    if not model.startswith("ollama_chat/"):
        return None

    raw_value = os.environ.get("OCEANUS_OLLAMA_NUM_CTX")
    if raw_value is None:
        if model == QWEN_Q4_MODEL:
            return QWEN_Q4_CONTEXT_TOKENS
        return None

    error_message = "OCEANUS_OLLAMA_NUM_CTX must be a positive integer"
    try:
        limit = int(raw_value)
    except ValueError:
        raise ValueError(error_message) from None
    if limit < 1:
        raise ValueError(error_message)
    return limit

def require_image_support(model: str) -> None:
    """ Stop image requests when model support is not confirmed """
    try:
        if model.startswith(("ollama_chat/", "ollama/")):
            model_name = model.split("/", 1)[1]
            supported = ollama_supports_images(model_name)
        else:
            supported = supports_vision(model=model)
    except (OSError, ValueError, HTTPException) as error:
        raise ModelResponseError("Couldn't check model image support. No completion request was sent!") from error

    if supported is not True:
        raise ModelResponseError(
            f"Image support is not confirmed for {model}. "
            "Choose a model that supports images or start a text-only conversation. "
            "No completion request was sent."
        )


def call_completion(
        conversation_history,
        model: str = DEFAULT_MODEL, 
        *, 
        use_tools: bool = True, 
        allowed_tools: set[str] | frozenset[str] | None = None,
        reasoning: str | bool | None = None,
        output_tokens: int | None = None,
        timeout_seconds: float | None = None,
        allow_retries: bool = True,
):
    """ Make one model request and return full response """
    """ Preserve Ollama's native stop reason """
    if type(allow_retries) is not bool:
        raise ValueError("allow_retries must be a boolean!")

    if reasoning is not None:
        if not model.startswith("ollama_chat/"):
            raise ModelResponseError("Reasoning selection currently supports local Ollama models only")
        if not isinstance(reasoning, bool):
            if not isinstance(reasoning, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", reasoning) is None:
                raise ModelResponseError("Invalid reasoning mode")

    # Summary can use its own allowance without changing chat defaults
    # Choose between normal allowance or caller's override (summarize)
    if output_tokens is None:
        output_limit = get_max_completion_tokens(model)
    else:
        if type(output_tokens) is not int or output_tokens < 1:
            raise ValueError("output_tokens must be a positive integer!")
        output_limit = output_tokens

    # Timeout validation
    if timeout_seconds is not None:
        if type(timeout_seconds) not in (int, float):
            raise ValueError("timeout_seconds must be a positive finite number!")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number!")

    context_tokens = get_context_tokens(model)
    # Input and generated tokens must share the context window
    if context_tokens is not None and output_limit >= context_tokens:
        raise ModelResponseError(
            "The output allowance must be smaller than the Ollama context "
            "configured by OCEANUS_OLLAMA_NUM_CTX or the model's Oceanus default. "
            "Leave room for the prompt and tool results. No request was sent."
        )
    tools = get_tool_schemas(allowed_tools) if use_tools else []
    request_messages = prepare_model_messages(conversation_history, tools)

    for message in request_messages:
        if isinstance(message.get("content"), list):
            require_image_support(model)
            break
    
    is_ollama = model.startswith("ollama_chat/")
    native_reason = None
    native_prompt_tokens = None
    native_output_tokens = None

    def capture_ollama_status(details):
        """ Called by LiteLLM before and after its HTTP request """
        nonlocal native_reason, native_prompt_tokens, native_output_tokens
        event = details.get("log_event_type")
        if event == "pre_api_call":
            native_reason = None
            native_prompt_tokens = None
            native_output_tokens = None
            return
        if event != "post_api_call":
            return

        native_reason = None
        native_prompt_tokens = None
        native_output_tokens = None
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
            prompt_tokens = raw.get("prompt_eval_count")
            output_tokens = raw.get("eval_count")
            if type(prompt_tokens) is int and prompt_tokens >= 0:
                native_prompt_tokens = prompt_tokens
            if type(output_tokens) is int and output_tokens >= 0:
                native_output_tokens = output_tokens

    extra_options = {"logger_fn": capture_ollama_status} if is_ollama else {}
    if context_tokens is not None:
        extra_options["num_ctx"] = context_tokens
    thinking = default_reasoning(model) if reasoning is None else reasoning
    if thinking is not None:
        # Use Ollama's native field so LiteLLM preserves the named thinking level
        extra_options["think"] = thinking

    # Omit the option when the caller wants the provider's normal timeout
    if timeout_seconds is not None:
        extra_options["timeout"] = timeout_seconds

    if not allow_retries:
        # Disable SDK retries and LiteLLM's outer retry policy
        extra_options["max_retries"] = 0
        extra_options["num_retries"] = 0
        extra_options["retry_policy"] = {"DefaultRetries": 0}

    response = completion(
        model=model,
        messages=request_messages,
        tools=tools or None,
        max_completion_tokens=output_limit,
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
            # Ollama also reports "length" when the context fills before output does
            if (context_tokens is not None and native_prompt_tokens is not None
                    and native_output_tokens is not None):
                used_tokens = native_prompt_tokens + native_output_tokens
                if used_tokens >= context_tokens and native_output_tokens < output_limit:
                    raise ModelContextLimitError(
                        "Stopped: the model's context window is full. Ollama reported "
                        f"{native_prompt_tokens:,} input tokens and {native_output_tokens:,} "
                        f"generated tokens for a {context_tokens:,}-token context. "
                        f"The {output_limit:,}-token output allowance was not exhausted. "
                        "The incomplete response was discarded; no tools from that response "
                        "were executed. Earlier tool actions, if any, were not undone. "
                        "Start a new session with a concise task summary, or increase "
                        "OCEANUS_OLLAMA_NUM_CTX if GPU memory permits. "
                        "Increasing only the output allowance will not make room for this history."
                    )

    return response


def call_model(
    conversation_history,
    model: str = DEFAULT_MODEL,
    *,
    allowed_tools: set[str] | frozenset[str] | None = None,
    reasoning: str | bool | None = None,
):
    """ Return assistant message for agent loop """
    response = call_completion(
        conversation_history, model=model,
        allowed_tools=allowed_tools, reasoning=reasoning,
    )
    choice = response.choices[0]

    if choice.finish_reason == "length":
        raise ModelOutputTruncatedError(
            "Stopped: the model reported a length limit. "
            f"The configured output limit is {get_max_completion_tokens(model):,} tokens for one response. "
            "Reasoning tokens also use the output allowance on thinking models. "
            "The incomplete response was discarded; no tools from that response were executed. "
            "Earlier tool actions, if any, were not undone. "
            "Increase OCEANUS_MAX_COMPLETION_TOKENS or request a smaller change before trying again. "
            "If the context is full, shorten the conversation or increase its context allocation instead."
        )

    return choice.message
