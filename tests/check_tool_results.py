from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_core.loop import run_agent


function = {
    "name": "read_file",
    "arguments": '{"path": "example.txt"}',
}

tool_call = SimpleNamespace(
    id="call_1",
    function=SimpleNamespace(**function),
)

request = Mock(content=None, tool_calls=[tool_call])
request.model_dump.return_value = {
    "role": "assistant",
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": function,
        }
    ],
}

reply = Mock(content="Done.", tool_calls=[])
reply.model_dump.return_value = {
    "role": "assistant",
    "content": "Done.",
}

results = []

def capture_result(name: str, result: str) -> None:
    results.append((name, result))

handler = Mock(return_value="Example contents")
history = []

with patch(
    "agent_core.loop.call_model",
    side_effect=[request, reply, request, reply],
), patch(
    "agent_core.loop.get_tool_function",
    return_value=handler,
):
    answer = run_agent(
        "Read the file.",
        conversation_history=history,
        on_tool_result=capture_result,
    )

    assert answer == "Done."
    assert results == [("read_file", "Example contents")]
    assert history[-2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Example contents",
    }

    handler.side_effect = FileNotFoundError("Missing file")

    run_agent("Read a missing file.", on_tool_result=capture_result)

    assert results[-1] == (
        "read_file",
        "Tool error: FileNotFoundError: Missing file",
    )

print("Tool-result checks passed.")