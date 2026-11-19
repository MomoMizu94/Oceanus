""" Prepare a smaller-response retry without changing saved instructions """

from copy import deepcopy


MAX_OUTPUT_RECOVERY_ATTEMPTS = 1

RECOVERY_FINAL_GUIDANCE = """[Oceanus runtime] The tool budget is now exhausted. Reply to the user in text.
Use confirmed file changes and test results to explain what works, how to use it,
and what still needs checking. The discarded response did not execute any tools."""

RECOVERY_STEP_GUIDANCE = """Continue the original task in small working increments.
If the next change needs a large file, write the first runnable piece now, aiming
for at most about 80 lines in this tool call. As soon as that piece runs, connect
it to the user's normal entry point and exercise that path. Then add remaining
behaviors through focused edits and test them.
Use the code already inspected. Read more only to answer a specific missing detail.
If the user asked only for an explanation, give the concise answer directly."""

RECOVERY_GUIDANCE = """Oceanus runtime recovery notice:
Your previous response reached its output limit and was discarded in full.
None of that response's tool calls ran. Earlier completed tool actions still stand.
Continue the user's existing task from the complete messages and tool results below.
Do not continue an unfinished JSON fragment or repeat actions already confirmed complete.
Keep the next response concise and avoid repeating analysis already established.
If tools are needed, request only the next small, complete operation. Prefer a targeted
edit over rewriting a whole file, and split larger changes across subsequent turns.
For an implementation task, choose one independently checkable change now. For
a new module, start with a minimal working implementation that uses existing
interfaces. Connect that version to the existing entry point before expanding
it. Add the remaining behavior through later edits and test the result.
Put the implementation in the tool call. Keep any preceding explanation short.
If no tool is needed, give a short, complete answer. Follow all existing instructions,
permissions, and tool restrictions. Do not claim that discarded work was completed.
"""


def build_recovery_request(messages: list[dict], *, guidance: str = RECOVERY_GUIDANCE) -> list[dict]:
    """ Add temporary guidance to a separate request copy """
    request = deepcopy(messages)
    if request and request[0].get("role") == "system":
        # Keep one leading system message for providers that expect that shape
        original_content = request[0].get("content") or ""
        request[0]["content"] = original_content + "\n\n" + guidance
    else:
        request.insert(0, {"role": "system", "content": guidance})
    return request


def qwen_recovery_context(messages: list[dict]) -> list[dict]:
    """Build Qwen's recovery input without replaying earlier assistant reasoning."""
    request = []
    for message in messages:
        if message.get("role") == "assistant" and "reasoning_content" in message:
            message = message.copy()
            del message["reasoning_content"]
        request.append(message)
    return request
