"""Describe and approve one page request"""
from agent_core.fetch_runtime import FETCH_TOTAL_TIMEOUT
from agent_core.pdf_read_limits import MAX_PDF_INPUT_BYTES, MAX_PDF_READ_CHARS, PDF_READ_TIMEOUT
from agent_core.tools.fetch import (
    MAX_FETCH_RESPONSE_BYTES,
    MAX_FETCH_TEXT_CHARS,
    parse_fetch_url,
    validate_fetch_host,
)


def describe_page_fetch(url: str) -> str:
    """Describe the destination and data shared by this request"""
    return _describe_fetch(url, pdf=False)


def describe_pdf_fetch(url: str) -> str:
    return _describe_fetch(url, pdf=True)


def _describe_fetch(url: str, *, pdf: bool) -> str:
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

    scope = "one page request"
    limits = (
        f"{MAX_FETCH_RESPONSE_BYTES} downloaded bytes, "
        f"{MAX_FETCH_TEXT_CHARS} returned characters, "
        f"{FETCH_TOTAL_TIMEOUT:g} seconds for the worker job."
    )
    pdf_details = ""
    if pdf:
        scope = "one PDF download and offline page extraction"
        limits = (
            f"{MAX_PDF_INPUT_BYTES} downloaded bytes (10 MiB), "
            f"{MAX_PDF_READ_CHARS} returned characters, "
            f"{FETCH_TOTAL_TIMEOUT:g} seconds to download and "
            f"{PDF_READ_TIMEOUT:g} seconds to extract."
        )
        pdf_details = (
            "PDF bytes stay in memory; no PDF file is saved. The selected page number "
            "and document hash also reach the model and session. Each continuation "
            "downloads the entire PDF again and uses the current permission mode. "
            "No OCR is performed.\n"
        )

    return (
        f"Exact URL: {url!r}\n"
        f"Destination: {parts.scheme}://{authority}\n"
        f"Scope: {scope}. Redirects and private/local addresses are blocked.\n"
        f"Limits: {limits}\n"
        f"{pdf_details}"
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


def describe_page_render(url: str) -> str:
    from agent_core.browser_render import MAX_REQUESTS, MAX_TOTAL_BYTES
    parts = parse_fetch_url(url)
    host = validate_fetch_host(parts.hostname)
    return (
        f"Render {url!r} in an isolated browser. Oceanus may send up to {MAX_REQUESTS} GET requests "
        f"to the same origin ({parts.scheme}://{host}) for HTML, scripts, styles and data. "
        f"The total download budget is {MAX_TOTAL_BYTES // 1048576} MiB over {FETCH_TOTAL_TIMEOUT:g} seconds. "
        "The site receives your IP and requested paths and queries. "
        "Oceanus omits browser cookies and credentials and blocks private addresses. Data requests may follow up to three same-origin redirects. "
        f"The model and session receive the source URL and up to {MAX_FETCH_TEXT_CHARS} characters of untrusted page text."
    )


def approve_page_render(url: str) -> bool:
    print(describe_page_render(url))
    try:
        return input("Render this page? [y/N]: ").strip().lower() == "y"
    except EOFError:
        return False


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


def approve_pdf_fetch(url: str) -> bool:
    description = describe_pdf_fetch(url)
    print("\nPDF fetch requested:")
    print(description)
    try:
        answer = input("Fetch this PDF? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"
