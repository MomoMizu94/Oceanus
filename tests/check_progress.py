from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from agent_core.loop import run_agent


reply = Mock(content="Done.", tool_calls=[])
reply.model_dump.return_value = {
    "role": "assistant",
    "content": "Done.",
}

messages = []
output = StringIO()

with patch("agent_core.loop.call_model", return_value=reply):
    with redirect_stdout(output):
        answer = run_agent(
            "Test progress reporting.",
            max_turns=2,
            on_progress=messages.append,
        )

        # Omitting the callback should also work.
        silent_answer = run_agent("Test without a callback.")

assert answer == "Done."
assert silent_answer == "Done."
assert messages == ["This is model turn: 1/2"]
assert output.getvalue() == ""

print("Progress checks passed.")