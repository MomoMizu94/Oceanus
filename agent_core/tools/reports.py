""" Export an existing Markdown report to a new PDF file """
import json
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

from agent_core.guardrails import resolve_workspace_path
from agent_core.report_limits import MAX_MARKDOWN_CHARS, MAX_PDF_BYTES, MAX_REPORT_TITLE_CHARS
from agent_core.report_runtime import render_report_pdf_in_worker
from agent_core.tools.edits import read_edit_snapshot


def _require_missing_file(name: str | Path, *, directory: int | None = None) -> None:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileExistsError("PDF destination already exists; choose a new filename.")


def _publish_pdf(
    directory: int,
    name: str,
    pdf: bytes,
    recheck: Callable[[], None],
) -> None:
    temporary_name = ".oceanus-pdf-" + secrets.token_hex(16)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            for start in range(0, len(pdf), 65_536):
                recheck()
                output.write(pdf[start:start + 65_536])
            output.flush()
            os.fsync(output.fileno())

        recheck()
        # Linking publishes the complete file and atomically refuses overwrites
        os.link(
            temporary_name, name,
            src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False,
        )
    finally:
        os.unlink(temporary_name, dir_fd=directory)


def export_pdf(
    source_path: str,
    path: str,
    title: str = "Oceanus report",
    *,
    workspace_root: Path,
    on_file_approval: Callable[[str, str], bool] | None = None,
    on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("PDF export cancelled before publication.")

    check_cancelled()
    if not sys.platform.startswith("linux"):
        raise OSError("PDF export currently requires Linux!")
    for value in (source_path, path, title):
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError("PDF paths and title must be nonblank text without NUL characters.")
    if len(title) > MAX_REPORT_TITLE_CHARS:
        raise ValueError("PDF title exceeds the character limit.")
    if Path(path).suffix.lower() != ".pdf":
        raise ValueError("PDF destination must have a .pdf extension.")

    root = workspace_root.resolve()
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    _require_missing_file(requested)
    sensitive_writes = set()

    def approve_file(operation: str, resolved: str) -> bool:
        check_cancelled()
        decision = False
        if on_file_approval is not None:
            decision = on_file_approval(operation, resolved)
        check_cancelled()
        return decision is True

    def approve_sensitive(operation: str, resolved: str, reason: str) -> bool:
        check_cancelled()
        decision = False
        if on_sensitive_approval is not None:
            decision = on_sensitive_approval(operation, resolved, reason)
        check_cancelled()
        if decision is True and operation == "write":
            sensitive_writes.add((resolved, reason))
        return decision is True

    # Reuse bounded UTF-8 reading and its external/sensitive approval checks
    snapshot = read_edit_snapshot(
        source_path, workspace_root=root,
        on_file_approval=approve_file, on_sensitive_approval=approve_sensitive,
    )
    markdown = snapshot["content"]
    if not markdown.strip() or len(markdown) > MAX_MARKDOWN_CHARS:
        raise ValueError("Markdown report is blank or exceeds the character limit.")
    check_cancelled()
    target = resolve_workspace_path(
        str(requested), root, operation="write",
        on_file_approval=approve_file, on_sensitive_approval=approve_sensitive,
    )
    _require_missing_file(target)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_info = os.fstat(directory)

        def approved_write(operation: str, resolved: str) -> bool:
            return operation == "write" and resolved == str(target)

        def approved_sensitive_write(operation: str, resolved: str, reason: str) -> bool:
            return operation == "write" and (resolved, reason) in sensitive_writes

        def recheck() -> None:
            check_cancelled()
            current = resolve_workspace_path(
                str(requested), root, operation="write",
                on_file_approval=approved_write,
                on_sensitive_approval=approved_sensitive_write,
            )
            if current != target:
                raise PermissionError("PDF destination changed; request export again.")
            current_info = target.parent.stat()
            if (current_info.st_dev, current_info.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                raise PermissionError("PDF destination directory changed; request export again.")
            _require_missing_file(target.name, directory=directory)
            check_cancelled()

        recheck()
        pdf = render_report_pdf_in_worker(markdown, title=title, should_cancel=should_cancel)
        if not isinstance(pdf, bytes) or len(pdf) > MAX_PDF_BYTES:
            raise OSError("PDF worker returned invalid or oversized output.")
        if not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF"):
            raise OSError("PDF worker returned incomplete output.")
        recheck()
        result = json.dumps({
            "source_path": snapshot["path"],
            "source_sha256": snapshot["sha256"],
            "path": str(target),
            "media_type": "application/pdf",
            "bytes_written": len(pdf),
        }, ensure_ascii=False)
        _publish_pdf(directory, target.name, pdf, recheck)
        return result
    finally:
        os.close(directory)
