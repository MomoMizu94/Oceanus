# Call the model, execute requested tools, append results, and repeat until done
import json
from importlib.resources import files

from agent_core.model_client import call_model
from agent_core.tools.registry import get_tool_function

def run_agent(task: str, max_turns: int = 10, system_prompt: str = "") -> str:
    """ Run a task until model answers or hits limit """
    if max_turns < 1:
        raise ValueError ("max_turns must be at least 1")

    conversation_history = []

    if system_prompt:
        conversation_history.append(
            {
                "role": "developer",
                "content": system_prompt
            }
        )

    conversation_history.append(
        {
            "role": "user",
            "content": task
        }
    )

    for turn in range(1, max_turns + 1):
        print(f"This is model turn: {turn}/{max_turns}")

        reply = call_model(conversation_history)
        # Preserve agent's tool requests before adding to result
        conversation_history.append(reply.model_dump(exclude_none=True))

        # If no tool requests -> task finished
        if not reply.tool_calls:
            return reply.content or ""

        # If not turns left & tool call is requested -> exit
        if turn == max_turns:
            break

        # Show explanations regarding the requested tools
        if reply.content:
            print("Agent: ", reply.content)

        for tool_call in reply.tool_calls:
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

    return (
        f"Stopped after {max_turns} was hit: the model still required more tool calls."
    )


if __name__ == "__main__":
        coding_prompt = (
            files("agents.coding")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )
        
        answer = run_agent(
            "Read agent_core/tools/files.py and explain its available tools. "
            "Do not edit any files.",
            system_prompt=coding_prompt
        )
        print("Agent: ", answer)