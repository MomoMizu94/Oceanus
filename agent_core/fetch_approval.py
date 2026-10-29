"""Describe and approve one page request"""
from agent_core.fetch_runtime import FETCH_TOTAL_TIMEOUT
from agent_core.tools.fetch import (
    MAX_FETCH_RESPONSE_BYTES,
    MAX_FETCH_TEXT_CHARS,
    parse_fetch_url,
    validate_fetch_host,
)


def describe_page_fetch(url: str) -> str:
    """Describe the destination and data shared by this request"""
    parts = parse_fetch_url(url)
    hostname = validate_fetch_host(parts.hostname)
    if parts.path.startswith("//"):
        raise ValueError("Page URL path must not start with //.")

    # Show the protocol and host separately from the full URL
    authority = hostname
    if ":" in hostname:
        authority = f"[{hostname}]"
    transport = "HTTPS verifies the server certificate."
    if parts.scheme == "http":
        transport = "HTTP is unencrypted; others on the network may see the request and reply."

    return (
        f"Exact URL: {url!r}\n"
        f"Destination: {parts.scheme}://{authority}\n"
        "Scope: one page request. Redirects and private/local addresses are blocked.\n"
        f"Limits: {MAX_FETCH_RESPONSE_BYTES} downloaded bytes, "
        f"{MAX_FETCH_TEXT_CHARS} returned characters, "
        f"{FETCH_TOTAL_TIMEOUT:g} seconds for the worker job.\n"
        "The hostname may be sent to your DNS resolver. The website receives your "
        "network IP address, URL path, query, and fixed request headers. "
        "The URL fragment after # is not sent to the website.\n"
        f"{transport}\n"
        "No cookies, API keys, files, or conversation history are attached automatically. "
        "The URL itself may contain private information; review it.\n"
        "The source URL and extracted text reach the selected model and may be saved "
        "in the session. Page text is untrusted data.\n"
        "Approval applies to this fetch only. It does not approve linked pages, "
        "scripts, downloads of other resources, or shell networking."
    )


def approve_page_fetch(url: str) -> bool:
    """Ask for approval in the terminal"""
    description = describe_page_fetch(url)
    print("\nPage fetch requested:")
    print(description)
    try:
        answer = input("Fetch this page? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"
