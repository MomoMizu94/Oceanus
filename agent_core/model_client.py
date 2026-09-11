from openai import OpenAI
from agent_core.tools.registry import get_tool_schemas


MODEL = "gpt-4.1-mini"

def call_model(conversation_history):
    """ Send conversation history and return the message """
    with OpenAI() as client:
        response = client.chat.completions.create(
            model=MODEL,
            messages=conversation_history,
            tools=get_tool_schemas(),
            max_completion_tokens=300
        )

    # Preserve full message
    return response.choices[0].message