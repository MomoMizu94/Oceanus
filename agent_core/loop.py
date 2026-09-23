# Call the model, execute requested tools, append results, and repeat until done
import json
from collections.abc import Callable

from agent_core.model_client import call_model, DEFAULT_MODEL
from agent_core.tools.registry import get_tool_function

def run_agent(
        task: str,
        max_turns: int = 10,
        system_prompt: str = "",
        model: str = DEFAULT_MODEL,
        conversation_history: list[dict] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_approval: Callable[[str, str], bool] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None
        ) -> str:
    """ Run a task until model answers or hits limit """

    def report_progress(message: str) -> None:
        """ Helper function for callback """
        if on_progress is not None:
            on_progress(message)

    if max_turns < 1:
        raise ValueError ("max_turns must be at least 1")

    if conversation_history is None:
        conversation_history = []

    # if system prompt and list is empty -> append system prompt to it
    if system_prompt and not conversation_history:
        conversation_history.append(
            {
                "role": "system",
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
        report_progress(f"This is model turn: {turn}/{max_turns}")

        reply = call_model(conversation_history, model=model)

        # Don't save tool requests that will not be executed
        if reply.tool_calls and turn == max_turns:
            break

        # Preserve agent's tool requests before adding to result
        conversation_history.append(reply.model_dump(exclude_none=True))

        # If no tool requests -> task finished
        if not reply.tool_calls:
            return reply.content or ""

        # Show explanations regarding the requested tools
        if reply.content:
            report_progress(f"Agent: {reply.content}")

        for tool_call in reply.tool_calls:
            # Translate requests into function calls
            tool_name = tool_call.function.name
            report_progress(f"Running tool: {tool_name}")

            try:
                arguments = json.loads(tool_call.function.arguments)
                tool_function = get_tool_function(tool_name)
        
                if tool_name == "run_shell":
                    result = tool_function(**arguments, on_approval=on_approval)
                else:
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

            if on_tool_result is not None:
                on_tool_result(tool_name, result)

    return (
        f"Stopped after {max_turns} was hit: the model still required more tool calls."
    )