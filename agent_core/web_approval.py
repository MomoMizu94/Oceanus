from agent_core.tools.web import build_search_payload, MAX_SEARCH_RESULTS


def describe_web_search(query: str) -> str:
    """ Describe what leaves the machine when one search is approved """

    # Validation
    payload = build_search_payload(query)
    exact_query = payload["query"]

    return (
        "Provider: Tavily\n"
        "Destination: https://api.tavily.com/search\n"
        f"Exact query: {exact_query!r}\n"
        f"Scope: one basic web search, up to {MAX_SEARCH_RESULTS} results.\n"
        "The query and fixed search settings will be sent to Tavily.\n"
        "No files or conversation history are attached automatically. "
        "The query itself may still contain private information; review it.\n"
        "Search results reach the selected model and may be saved in the session.\n"
        "Approval allows this search only, not opening result pages or "
        "enabling shell networking."
    )

def approve_web_search(query: str) -> bool:
    """ Ask in terminal, approve with y or Y """
    description = describe_web_search(query)
    print("\nWeb search requested:")
    print(description)

    # An empty answer or closed input must not grant permission.
    try:
        answer = input("Send this query to Tavily? [y/N]: ")
    except EOFError:
        return False

    return answer.strip().lower() == "y"