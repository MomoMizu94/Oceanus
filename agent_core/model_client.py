from litellm import completion

from agent_core.tools.registry import get_tool_schemas


DEFAULT_MODEL = "openai/gpt-4.1-mini"


def call_completion(
        conversation_history, 
        model: str = DEFAULT_MODEL, 
        *, 
        use_tools: bool = True, 
        allowed_tools: set[str] | frozenset[str] | None = None
):
    """ Make one model request and return full response """
    tools = get_tool_schemas(allowed_tools) if use_tools else []
    
    return completion(
        model=model,
        messages=conversation_history,
        tools=tools or None,
        max_completion_tokens=300,
        stream=False
    )


def call_model(conversation_history, model: str = DEFAULT_MODEL, *, allowed_tools: set[str] | frozenset[str] | None = None):
    """ Return assistant message for agent loop """
    response = call_completion(conversation_history, model=model, allowed_tools=allowed_tools)
    return response.choices[0].message