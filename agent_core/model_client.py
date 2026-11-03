from litellm import completion, supports_vision
import os
import json
import re
from copy import deepcopy
from pathlib import Path
from http.client import HTTPException

from agent_core.tools.registry import get_tool_schemas
from agent_core.image_attachments import build_user_content, MAX_IMAGES_PER_MESSAGE
from agent_core.model_discovery import ollama_supports_images


DEFAULT_MODEL = "openai/gpt-4.1"
DEFAULT_MAX_COMPLETION_TOKENS = 4096
# Limits for the entire conversation sent in one request
MAX_REQUEST_IMAGES = 8
MAX_REQUEST_IMAGE_CHARS = 48 * 1024 * 1024


class ModelResponseError(RuntimeError):
    """ A model response cannot safely be used by agent """


class ModelOutputTruncatedError(ModelResponseError):
    """ Provider reported an incomplete response at its output limit """


DEFAULT_MAX_REQUEST_CHARS = 512_000


class RequestBudgetExceededError(ModelResponseError):
    """ Next request exceeds Oceanus' configured input-size budget """


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
        allowed_tools: set[str] | frozenset[str] | None = None
):
    """ Make one model request and return full response """
    """ Preserve Ollama's native stop reason """
    tools = get_tool_schemas(allowed_tools) if use_tools else []
    request_messages = prepare_model_messages(conversation_history, tools)

    for message in request_messages:
        if isinstance(message.get("content"), list):
            require_image_support(model)
            break
    
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
        messages=request_messages,
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
