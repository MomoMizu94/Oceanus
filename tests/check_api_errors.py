import json
from unittest.mock import patch

from litellm import APIConnectionError, Timeout

from agent_core.server.api import ChatRequest, chat_completions


request = ChatRequest(
    model="ollama_chat/qwen2.5:7b",
    messages=[{
        "role": "user",
        "content": "Hello"
    }]
)

cases = [
    (Timeout, 504, "model_timeout"),
    (APIConnectionError, 502, "model_connection_error")
]

for error_class, expected_status, expected_code in cases:
    error = error_class(
        message="Simulated failure",
        model="qwen2.5:7b",
        llm_provider="ollama_chat"
    )

    with patch("agent_core.server.api.call_completion", side_effect=error):
        response = chat_completions(request)

    body = json.loads(response.body)
    assert response.status_code == expected_status
    assert body["error"]["code"] == expected_code
    print(f"{error_class.__name__}: passed")