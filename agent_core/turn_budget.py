"""Add progress and turn-budget reminders to the current model request."""

from copy import deepcopy


PROGRESS_REMINDER = (
    "If you need more tools, include a brief progress update in the assistant text "
    "alongside your next tool calls. Tell the user one concrete finding or obstacle "
    "and what you are checking next. If the task is finished, give the final answer."
)


def build_turn_budget_request(
    messages: list[dict], turn: int, max_turns: int, *, progress_due: bool = False,
    first_edit_done: bool = False,
) -> list[dict]:
    tool_turns_left = max_turns - turn
    guidance = ""
    if tool_turns_left == 0:
        guidance = (
            f"Oceanus runtime turn budget: model turn {turn}/{max_turns}. "
            "There are 0 turns remaining that can execute tools. Give the user your final answer now. "
            "Describe the deliverable, how to use it, and any missing or unverified work. "
            "Do not propose a next tool action or promise another check."
        )
    elif tool_turns_left <= 5:
        guidance = (
            f"Oceanus runtime turn budget: model turn {turn}/{max_turns}. "
            f"There are {tool_turns_left} turns remaining that can execute tool requests, "
            "including this turn when that count is positive. Multiple tools may share a turn. "
            "The final model turn can only return text; its tool requests will not execute. "
            "Prioritize the user's remaining deliverable using verified results already obtained. "
            "Avoid repeating successful checks or expanding optional analysis. "
            "For a coding feature, check how the user will reach it. If a new module already runs "
            "on its own, use the next tool calls to wire it into the project's existing launcher, "
            "menu, or public interface where applicable. Exercise that path before polishing mechanics. "
            "For reports, write the verified source and perform the requested export before tools run out. "
            "State unresolved questions as limitations instead of inventing certainty. "
            "If completion is blocked, report the exact blocker and work still missing. "
            "All permission, validation, and verification rules still apply."
        )
    if progress_due and tool_turns_left > 0:
        guidance = (guidance + "\n\n" + PROGRESS_REMINDER).strip()
    if first_edit_done and tool_turns_left > 0:
        reminder = (
            "If the edit changes runnable behavior and you haven't checked it yet, "
            "exercise one changed path now, before updating documentation or repeating "
            "the change elsewhere. Use the smallest check that asserts the expected outcome."
        )
        guidance = (guidance + "\n\n" + reminder).strip()
    if not guidance:
        return messages
    request = deepcopy(messages)
    if request and request[0].get('role') == 'system':
        original_content = request[0].get('content') or ''
        request[0]['content'] = original_content + '\n\n' + guidance
    else:
        request.insert(0, {'role': 'system', 'content': guidance})
    return request
