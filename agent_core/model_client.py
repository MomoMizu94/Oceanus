from litellm import completion

from agent_core.tools.registry import get_tool_schemas


DEFAULT_MODEL = "openai/gpt-4.1-mini"


def call_completion(conversation_history, model: str = DEFAULT_MODEL, *, use_tools: bool = True):
    """ Make one model request and return full response """
    return completion(
        model=model,
        messages=conversation_history,
        tools=get_tool_schemas() if use_tools else None,
        max_completion_tokens=300,
        stream=False
    )


def call_model(conversation_history, model: str = DEFAULT_MODEL):
    """ Return only assistant message for agent loop. Extracts it from chat_completion. Used by agent loop """
    response = call_completion(conversation_history, model=model)
    return response.choices[0].message