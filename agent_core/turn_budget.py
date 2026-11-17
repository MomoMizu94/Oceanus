""" Give the model a temporary reminder before its tool-turn budget runs out """

from copy import deepcopy


def build_turn_budget_request(messages: list[dict], turn: int, max_turns: int) -> list[dict]:
    tool_turns_left = max_turns - turn
    if tool_turns_left > 5:
        return messages

    guidance = (
        f"Oceanus runtime turn budget: model turn {turn}/{max_turns}. "
        f"There are {tool_turns_left} turns remaining that can execute tool requests, "
        "including this turn when that count is positive. Multiple tools may share a turn. "
        "The final model turn can only return text; its tool requests will not execute. "
        "Prioritize the user's remaining deliverable using verified results already obtained. "
        "Avoid repeating successful checks or expanding optional analysis. "
        "For reports, write the verified source and perform the requested export before tools run out. "
        "State unresolved questions as limitations instead of inventing certainty. "
        "If completion is blocked, report the exact blocker and work still missing. "
        "All permission, validation, and verification rules still apply."
    )
    request = deepcopy(messages)
    if request and request[0].get('role') == 'system':
        original_content = request[0].get('content') or ''
        request[0]['content'] = original_content + '\n\n' + guidance
    else:
        request.insert(0, {'role': 'system', 'content': guidance})
    return request
