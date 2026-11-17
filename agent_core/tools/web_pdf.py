""" Download a public PDF and extract one page without saving a file """
import hashlib
import json
import sys
import time
from collections.abc import Callable

from agent_core.pdf_read_limits import PDF_READ_TIMEOUT
from agent_core.pdf_read_runtime import check_pdf_stop, extract_pdf_page
from agent_core.tools.fetch import parse_fetch_url, validate_fetch_host
from agent_core.tools.pdf import build_pdf_page_result, validate_pdf_selection


def fetch_pdf(
    url: str,
    page: int = 1,
    offset: int = 0,
    max_chars: int = 8000,
    expected_sha256: str | None = None,
    *,
    on_fetch_approval: Callable[[str], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    if not sys.platform.startswith("linux"):
        raise OSError("PDF reading currently requires Linux.")
    parts = parse_fetch_url(url)
    validate_fetch_host(parts.hostname)
    if parts.path.startswith("//"):
        raise ValueError("PDF URL path must not start with //.")
    validate_pdf_selection(page, offset, max_chars)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError("expected_sha256 must be null or a 64-character lowercase SHA-256 hash.")
        if any(character not in "0123456789abcdef" for character in expected_sha256):
            raise ValueError("expected_sha256 must contain lowercase hexadecimal characters.")

    # DNS and network access start only after approval
    check_pdf_stop(time.monotonic() + PDF_READ_TIMEOUT, should_cancel)
    approved = on_fetch_approval is not None and on_fetch_approval(url) is True
    check_pdf_stop(time.monotonic() + PDF_READ_TIMEOUT, should_cancel)
    if not approved:
        raise PermissionError("PDF fetch denied. No request was sent.")

    from agent_core.fetch_runtime import _collect_fetch_reply, _parse_fetch_pdf_reply

    reply = _collect_fetch_reply(url, kind="pdf", should_cancel=should_cancel)
    snapshot = _parse_fetch_pdf_reply(reply, url)
    deadline = time.monotonic() + PDF_READ_TIMEOUT
    check_pdf_stop(deadline, should_cancel)
    digest = hashlib.sha256(snapshot).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("PDF changed since the previous read. Start a new read before combining pages.")
    page_count, text = extract_pdf_page(
        snapshot, page, should_cancel=should_cancel, timeout=deadline - time.monotonic(),
    )
    check_pdf_stop(deadline, should_cancel)
    result = build_pdf_page_result(snapshot, page_count, text, page, offset, max_chars)
    result["url"] = url
    result["content_type"] = "application/pdf"
    return json.dumps(result, ensure_ascii=False)
