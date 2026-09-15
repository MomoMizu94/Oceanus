from litellm import completion

from agent_core.tools.registry import get_tool_schemas


DEFAULT_MODEL = "openai/gpt-4.1-mini"


def call_model(conversation_history, model: str = DEFAULT_MODEL):
    """ Send conversation history to selected model through LiteLLM. """
    response = completion(
        model=model,
        messages=conversation_history,
        tools=get_tool_schemas(),
        max_completion_tokens=300
    )

    # Return full message (with tool calls)
    return response.choices[0].message