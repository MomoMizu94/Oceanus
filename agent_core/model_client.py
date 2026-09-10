import json

from openai import OpenAI
from agent_core.tools.registry import get_tool_schemas, get_tool_function


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


if __name__ == "__main__":
    conversation_history = [
        {
            "role": "user",
            "content": "Read PLAN.md and tell me what Phase 1 involves."
        }
    ]

    reply = call_model(conversation_history)

    # Preserve agent's tool requests before adding to result
    conversation_history.append(reply.model_dump(exclude_none=True))

    for tool_call in reply.tool_calls or []:
        # Translate requests into function calls
        tool_name = tool_call.function.name
        print("Running tool:", tool_name)

        try:
            arguments = json.loads(tool_call.function.arguments)
            tool_function = get_tool_function(tool_name)
            result = tool_function(**arguments)
        except (ValueError, KeyError, TypeError, OSError) as error:
            # Report failed tool execution so model can respond
            result = f"Tool error: {type(error).__name__}: {error}"

        # Record results
        conversation_history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            }
        )

    # Make another request only if tool call gave results
    if reply.tool_calls:
        reply = call_model(conversation_history)

    if reply.tool_calls:
        print("The model requested more tools:", reply.tool_calls)
    else:
        print("Assistant reply:", reply.content)