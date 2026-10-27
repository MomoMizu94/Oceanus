import http.client
import json
import os
from collections.abc import Callable


MAX_QUERY_CHARS = 1000
MAX_SEARCH_RESULTS = 5
MAX_SEARCH_TITLE_CHARS = 200
MAX_SEARCH_SNIPPET_CHARS = 1000
MAX_SEARCH_URL_CHARS = 2048
MAX_SEARCH_RESPONSE_BYTES = 1_048_576
SEARCH_SOCKET_TIMEOUT = 15


def build_search_payload(query: str) -> dict:
    """ Validate a query and prepare its request """

    # Check the type
    if not isinstance(query, str):
        raise ValueError("Search query must be a string!")

    # Reject empty searches. Keep OG text for approval and sending
    if not query.strip():
        raise ValueError("Search query must not be blank!")

    # Oceanus' limit measured in characters
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Search query must be at most {MAX_QUERY_CHARS} characters!")

    # Require printable text on one line so the approval is easier to inspect
    # Ordinary spaces, punctuation and printable unicode text is allowed
    if not query.isprintable():
        raise ValueError("Search query must contain only printable characters!")

    # Build a new dict each time
    # Basic search, 5 results, without generated answers, full pages, or images
    return {
        "query": query,
        "topic": "general",
        "search_depth": "basic",
        "max_results": MAX_SEARCH_RESULTS,
        "auto_parameters": False,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False
    }


def parse_search_response(data: dict) -> dict:
    """ Keep a set of search results from Tavily JSON """

    # Missing results list
    if not isinstance(data, dict):
        raise ValueError("Search response must be an object!")

    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("Search response must contain a results list!")

    # Report when results are omitted or shortened
    parsed = {
        "results": [],
        "truncated": len(results) > MAX_SEARCH_RESULTS
    }

    # Process first 5 results, preserving order
    for result in results[:MAX_SEARCH_RESULTS]:
        if not isinstance(result, dict):
            raise ValueError("Each search result must be an object!")

        title = result.get("title")
        url = result.get("url")
        snippet = result.get("content")

        # Check every field before slicing or copying
        for value in (title, url, snippet):
            if not isinstance(value, str):
                raise ValueError("Search title, URL and content must be text!")

        # Never shorten a source url
        if not url.strip() or len(url) > MAX_SEARCH_URL_CHARS:
            raise ValueError("Search URL must be nonblank and at most 2048 characters!")

        title_truncated = len(title) > MAX_SEARCH_TITLE_CHARS
        snippet_truncated = len(snippet) > MAX_SEARCH_SNIPPET_CHARS

        if title_truncated or snippet_truncated:
            parsed["truncated"] = True

        # Copy necessary fields
        parsed["results"].append({
            "title": title[:MAX_SEARCH_TITLE_CHARS],
            "url": url,
            "snippet": snippet[:MAX_SEARCH_SNIPPET_CHARS],
            "title_truncated": title_truncated,
            "snippet_truncated": snippet_truncated
        })

    
    return parsed


def _request_tavily_search(query: str) -> dict:
    """ Send one search request. The caller must obtain approval first """

    # Reuse fixed settings
    payload = build_search_payload(query)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Read the key if needed
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise ValueError("TAVILY_API_KEY not set!")
    if not key.isascii() or not key.isprintable() or " " in key:
        raise ValueError("TAVILY_API_KEY contains invalid characters!")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity"
    }

    # Use one fixed HTTPS destination. Client doesn't follow redirects or load proxy settings from the environment
    connection = http.client.HTTPSConnection("api.tavily.com", timeout=SEARCH_SOCKET_TIMEOUT)
    try:
        connection.request("POST", "/search", body=body, headers=headers)
        with connection.getresponse() as response:
            # Don't echo provider error bodies - user these instead
            if response.status == 401:
                raise ValueError("Tavily rejected the API key!")
            if response.status in (429, 432, 433):
                raise ValueError("Tavily rate or usage limit reached!")
            if response.status != 200:
                raise ValueError(f"Tavily returned HTTP {response.status}.")

            # Reject other than uncompressed JSON
            encoding = response.getheader("Content-Encoding", "identity")
            if encoding.lower().strip() != "identity":
                raise ValueError("Tavily returned an unsupported content encoding!")

            # Detect oversized responses
            raw = response.read(MAX_SEARCH_RESPONSE_BYTES + 1)
            if len(raw) > MAX_SEARCH_RESPONSE_BYTES:
                raise ValueError("Tavily response exceeds the size limit!")

    except TimeoutError:
        raise TimeoutError("Tavily request timed out!") from None
    except (OSError, http.client.HTTPException):
        raise OSError("Could not complete the Tavily request.") from None
    finally:
        # Close the connection on success, rejected responses, and I/O errors
        connection.close()

    # Decode after the bounded download. Keep raw response text out of errors
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Tavily returned invalid JSON!") from None

    # Hand it to existing response parser
    return parse_search_response(data)

def web_search(query: str, *, on_web_approval: Callable[[str], bool] | None = None) -> str:
    """ Search Tavily only after approval of this query """

    # Validate before asking for approval. Invalid input -> no API query
    payload = build_search_payload(query)
    approved_query = payload["query"]

    # Missing handler -> deny
    if on_web_approval is None:
        raise PermissionError("Web search requires approval!")

    # Only bool True grants permission from the approval query
    decision = on_web_approval(approved_query)
    if decision is not True:
        raise PermissionError("Web search denied. No request was sent.")

    # Read the key and send the query to Tavily only after approval
    result = _request_tavily_search(approved_query)

    # Include the source and exact query beside the results
    output = {
        "provider": "tavily",
        "query": approved_query,
        "results": result["results"],
        "truncated": result["truncated"]
    }

    # Convert the results into a json string
    return json.dumps(output, ensure_ascii=False)