""" Export approved source snapshots to new report files """
import json
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

from agent_core.guardrails import resolve_workspace_path
from agent_core.report_canvas import build_canvas_document, MAX_CANVAS_SOURCE_CHARS
from agent_core.report_limits import MAX_MARKDOWN_CHARS, MAX_PDF_BYTES, MAX_REPORT_TITLE_CHARS
from agent_core.report_runtime import render_report_pdf_in_worker
from agent_core.tools.edits import read_edit_snapshot


def _require_missing_file(name: str | Path, *, directory: int | None = None) -> None:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileExistsError("Report destination already exists; choose a new filename.")


def _publish_report(
    directory: int,
    name: str,
    content: bytes,
    recheck: Callable[[], None],
) -> None:
    temporary_name = ".oceanus-report-" + secrets.token_hex(16)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            for start in range(0, len(content), 65_536):
                recheck()
                output.write(content[start:start + 65_536])
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


def _export_report(
    source_path: str,
    path: str,
    *,
    extension: str,
    media_type: str,
    source_limit: int,
    build: Callable[[str], tuple[bytes, dict]],
    workspace_root: Path,
    on_file_approval: Callable[[str, str], bool] | None = None,
    on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    require_write_approval: bool = False,
) -> str:
    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Report export cancelled before publication.")

    check_cancelled()
    if not sys.platform.startswith("linux"):
        raise OSError("Report export currently requires Linux!")
    for value in (source_path, path):
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError("Report paths must be nonblank text without NUL characters.")
    if Path(path).suffix.lower() != extension:
        raise ValueError(f"Report destination must have a {extension} extension.")

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
    source = snapshot["content"]
    if not source.strip() or len(source) > source_limit:
        raise ValueError(f"Report source is blank or exceeds {source_limit} characters.")
    check_cancelled()
    target = resolve_workspace_path(
        str(requested), root, operation="write",
        on_file_approval=approve_file, on_sensitive_approval=approve_sensitive,
        require_write_approval=require_write_approval,
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
                raise PermissionError("Report destination changed; request export again.")
            current_info = target.parent.stat()
            if (current_info.st_dev, current_info.st_ino) != (directory_info.st_dev, directory_info.st_ino):
                raise PermissionError("Report destination directory changed; request export again.")
            _require_missing_file(target.name, directory=directory)
            check_cancelled()

        recheck()
        content, details = build(source)
        recheck()
        result = json.dumps({
            "source_path": snapshot["path"],
            "source_sha256": snapshot["sha256"],
            "path": str(target),
            "media_type": media_type,
            "bytes_written": len(content),
            **details,
        }, ensure_ascii=False)
        _publish_report(directory, target.name, content, recheck)
        return result
    finally:
        os.close(directory)


def export_pdf(
    source_path: str,
    path: str,
    title: str = "Oceanus report",
    *,
    workspace_root: Path,
    on_file_approval: Callable[[str, str], bool] | None = None,
    on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    require_write_approval: bool = False,
) -> str:
    if not isinstance(title, str) or not title.strip() or "\x00" in title or len(title) > MAX_REPORT_TITLE_CHARS:
        raise ValueError("PDF title must be nonblank text of at most 200 characters without NUL characters.")

    def build(markdown: str) -> tuple[bytes, dict]:
        pdf = render_report_pdf_in_worker(markdown, title=title, should_cancel=should_cancel)
        if not isinstance(pdf, bytes) or len(pdf) > MAX_PDF_BYTES:
            raise OSError("PDF worker returned invalid or oversized output.")
        if not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF"):
            raise OSError("PDF worker returned incomplete output.")
        return pdf, {}

    return _export_report(
        source_path, path, extension=".pdf", media_type="application/pdf",
        source_limit=MAX_MARKDOWN_CHARS, build=build, workspace_root=workspace_root,
        on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval,
        should_cancel=should_cancel, require_write_approval=require_write_approval,
    )


def export_canvas(
    source_path: str,
    path: str,
    *,
    workspace_root: Path,
    on_file_approval: Callable[[str, str], bool] | None = None,
    on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    require_write_approval: bool = False,
) -> str:
    return _export_report(
        source_path, path, extension=".canvas", media_type="application/json",
        source_limit=MAX_CANVAS_SOURCE_CHARS, build=build_canvas_document, workspace_root=workspace_root,
        on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval,
        should_cancel=should_cancel, require_write_approval=require_write_approval,
    )
