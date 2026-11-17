""" Read a selected PDF page through the normal file permission rules """
import hashlib
import json
import os
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path

from agent_core.guardrails import resolve_workspace_path
from agent_core.pdf_read_limits import (
    MAX_PDF_INPUT_BYTES, MAX_PDF_PAGES, MAX_PDF_READ_CHARS, PDF_READ_TIMEOUT,
)
from agent_core.pdf_read_runtime import check_pdf_stop, extract_pdf_page


def read_pdf(
    path: str,
    page: int = 1,
    offset: int = 0,
    max_chars: int = 8000,
    *,
    workspace_root: Path,
    on_file_approval: Callable[[str, str], bool] | None = None,
    on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    if not sys.platform.startswith("linux"):
        raise OSError("PDF reading currently requires Linux.")
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("PDF path must be nonblank text without NUL characters.")
    if path.lower().startswith(("http://", "https://")):
        raise ValueError("read_pdf accepts local files, not web URLs.")
    validate_pdf_selection(page, offset, max_chars)

    check_pdf_stop(time.monotonic() + PDF_READ_TIMEOUT, should_cancel)
    target = resolve_workspace_path(
        path, workspace_root, operation="read",
        on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval,
    )
    # Waiting for approval does not consume the extraction deadline
    deadline = time.monotonic() + PDF_READ_TIMEOUT
    check_pdf_stop(deadline, should_cancel)
    descriptor = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("PDF reading requires a regular file.")
        if before.st_size > MAX_PDF_INPUT_BYTES:
            raise ValueError("PDF exceeds the 10 MiB input limit.")
        data = bytearray()
        while len(data) <= MAX_PDF_INPUT_BYTES:
            check_pdf_stop(deadline, should_cancel)
            allowance = min(65_536, MAX_PDF_INPUT_BYTES + 1 - len(data))
            chunk = os.read(descriptor, allowance)
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError("PDF changed while being read; request it again.")
    finally:
        os.close(descriptor)
    check_pdf_stop(deadline, should_cancel)
    if len(data) > MAX_PDF_INPUT_BYTES:
        raise ValueError("PDF exceeds the 10 MiB input limit.")
    snapshot = bytes(data)
    page_count, text = extract_pdf_page(
        snapshot, page, should_cancel=should_cancel,
        timeout=deadline - time.monotonic(),
    )
    check_pdf_stop(deadline, should_cancel)
    result = build_pdf_page_result(snapshot, page_count, text, page, offset, max_chars)
    result["path"] = str(target)
    return json.dumps(result, ensure_ascii=False)


def validate_pdf_selection(page: int, offset: int, max_chars: int) -> None:
    if type(page) is not int or not 1 <= page <= MAX_PDF_PAGES:
        raise ValueError(f"page must be an integer between 1 and {MAX_PDF_PAGES}.")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer.")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_PDF_READ_CHARS:
        raise ValueError(f"max_chars must be an integer between 1 and {MAX_PDF_READ_CHARS}.")


def build_pdf_page_result(
    snapshot: bytes, page_count: int, text: str, page: int, offset: int, max_chars: int,
) -> dict:
    if offset > len(text):
        raise ValueError("offset is beyond this page's extracted text; start at offset 0.")
    content = text[offset:offset + max_chars]
    end = offset + len(content)
    page_truncated = end < len(text)
    if page_truncated:
        next_page = page
        next_offset = end
    elif page < page_count:
        next_page = page + 1
        next_offset = 0
    else:
        next_page = None
        next_offset = None

    text_status = "text" if text.strip() else "no_extractable_text"
    warnings = ["Text extraction does not describe images or guarantee table, equation, or column layout."]
    if text_status == "no_extractable_text":
        warnings.append("This page may be scanned, image-only, or blank. No OCR was performed.")
    return {
        "sha256": hashlib.sha256(snapshot).hexdigest(),
        "page": page,
        "page_count": page_count,
        "offset": offset,
        "content": content,
        "page_truncated": page_truncated,
        "next_page": next_page,
        "next_offset": next_offset,
        "text_status": text_status,
        "warnings": warnings,
    }
