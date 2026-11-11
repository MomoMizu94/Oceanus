""" Prepare a smaller-response retry without changing saved instructions """

from copy import deepcopy


MAX_OUTPUT_RECOVERY_ATTEMPTS = 1

RECOVERY_GUIDANCE = """Oceanus runtime recovery notice:
Your previous response reached its output limit and was discarded in full.
None of that response's tool calls ran. Earlier completed tool actions still stand.
Continue the user's existing task from the complete messages and tool results below.
Do not continue an unfinished JSON fragment or repeat actions already confirmed complete.
Keep the next response concise and avoid repeating analysis already established.
If tools are needed, request only the next small, complete operation. Prefer a targeted
edit over rewriting a whole file, and split larger changes across subsequent turns.
If no tool is needed, give a short, complete answer. Follow all existing instructions,
permissions, and tool restrictions. Do not claim that discarded work was completed.
"""


def build_recovery_request(messages: list[dict]) -> list[dict]:
    """ Add temporary guidance to a separate request copy """
    request = deepcopy(messages)
    if request and request[0].get("role") == "system":
        # Keep one leading system message for providers that expect that shape
        original_content = request[0].get("content") or ""
        request[0]["content"] = original_content + "\n\n" + RECOVERY_GUIDANCE
    else:
        request.insert(0, {"role": "system", "content": RECOVERY_GUIDANCE})
    return request
