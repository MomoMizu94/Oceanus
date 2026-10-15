import hashlib
import os
import stat
from collections.abc import Callable
from difflib import unified_diff
from pathlib import Path
from tempfile import NamedTemporaryFile

from agent_core.guardrails import resolve_workspace_path


MAX_DIFF_CHARS = 16_000
MAX_EDIT_BYTES = 1_000_000

def replace_once(original: str, old_text: str, new_text: str) -> str:
    """ Replace one literal match. Preserve surrounding text """
    if not all(isinstance(value, str) for value in (original, old_text, new_text)):
        raise TypeError("original, old_text and new_text must all be strings")

    if not old_text:
        raise ValueError("old_text must not be empty")

    if old_text == new_text:
        raise ValueError("old_text and new_text must be different")

    # Find requested text
    start = original.find(old_text)
    # If missing -> reject edit
    if start == -1:
        raise ValueError("old_text was not found; read the current file before editing")

    # If another match, search for more surrounding text to determine which is the one that needed edits
    if original.find(old_text, start + 1) != -1:
        raise ValueError("old_text matches more than once; include more surrounding text")

    end = start + len(old_text)
    # Return og prefix, replacement and original suffix
    return original[:start] + new_text + original[end:]

def build_edit_preview(original: str, updated: str, *, max_chars: int = MAX_DIFF_CHARS) -> dict:
    """ Return bounded diff for display, without changing anything """
    if not all(isinstance(value, str) for value in (original,updated)):
        raise TypeError("original and updated must be strings")

    if type(max_chars) is not int or not 1 <= max_chars <= MAX_DIFF_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_DIFF_CHARS}")

    # og and updated text comparison, prefixing and inclusion of 3 surrounding lines for context
    lines = unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile="original",
        tofile="updated",
        n=3
    )

    parts = []
    remaining = max_chars + 1

    for line in lines:
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"

        piece = line[:remaining]
        parts.append(piece)
        remaining -= len(piece)

        if remaining == 0:
            break

    preview = "".join(parts)
    return {
        "diff": preview[:max_chars],
        "truncated": len(preview) > max_chars
    }

def read_edit_snapshot(
        path: str,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> dict:
    """ Read a bounded UTF-8 snapshot without changing the file """
    # Resolve path & approval request
    file_path = resolve_workspace_path(path, workspace_root, operation="read", on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval)

    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    # Open file for reading
    descriptor = os.open(file_path, flags)

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Editin requires a normal file")

        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_EDIT_BYTES + 1)

    finally:
        os.close(descriptor)

    if len(raw) > MAX_EDIT_BYTES:
        raise ValueError(f"File exceeds the {MAX_EDIT_BYTES}-byte editing limit")

    content = raw.decode("utf-8")
    if "\x00" in content:
        raise ValueError("Editing does not support text containing NUL characters")

    return {
        # Content with SHA-256 fingerprint
        "path": str(file_path),
        "content": content,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }

def prepare_edit(
        path: str,
        old_text: str,
        new_text: str,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> dict:
    """ Prepare a complete edit for review without writing to disk """
    # Read original file through approval checks
    snapshot = read_edit_snapshot(path, workspace_root=workspace_root, on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval)
    # Replace the requested text in memory
    updated = replace_once(snapshot["content"], old_text, new_text)

    # Checks if within byte limit
    if "\x00" in updated:
        raise ValueError("Editing does not support text containing NUL characters")
    if len(updated.encode("utf-8")) > MAX_EDIT_BYTES:
        raise ValueError(f"Edited file exceeds the {MAX_EDIT_BYTES}-byte eiditing limit")

    # Diff generation. Rejection if not able to show completely.
    preview = build_edit_preview(snapshot["content"], updated)
    if preview["truncated"]:
        raise ValueError("Edit diff too large to review; make a smaller edit")

    # Return proposed content, diff and OG fingerprint
    proposal = {
        "path": snapshot["path"],
        "original_sha256": snapshot["sha256"],
        "updated_content": updated,
        "diff": preview["diff"]
    }

    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = workspace_root.resolve() / requested

    if str(requested) != snapshot["path"]:
        proposal["access_path"] = str(requested)

    return proposal

def verify_edit_snapshot(
        proposal: dict,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
) -> dict:
    """ Check an edit proposal against current file contents """
    # Rechecks proposal
    current = read_edit_snapshot(
        proposal.get("access_path", proposal["path"]), # Acess path if present. Otherwise proposal path
        workspace_root=workspace_root,
        on_file_approval=on_file_approval,
        on_sensitive_approval=on_sensitive_approval
    )

    # Checks path & compares sha256 fingerprints of current contents and one returned by prepare_edit
    if current["path"] != proposal["path"]:
        raise ValueError("File path changed! Prepare and review a new edit")
    if current["sha256"] != proposal["original_sha256"]:
        raise ValueError("File contents changed! Prepare and review a new edit")

    return current

def apply_edit(
        proposal: dict,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_edit_approval: Callable[[str, str], bool] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None
        ) -> str:
    """ Apply an internal proposal after diff approval and a fresh file check """

    def check_cancelled():
        if should_cancel is not None and should_cancel():
            raise PermissionError("Edit cancelled. Nothing was written!")

    check_cancelled()

    if on_edit_approval is None:
        raise PermissionError("Edit approval is required")

    proposal = dict(proposal)
    current = verify_edit_snapshot(proposal, workspace_root=workspace_root, on_file_approval=on_file_approval, on_sensitive_approval=on_sensitive_approval)

    updated = proposal["updated_content"]
    if not isinstance(updated, str):
        raise TypeError("updated_content must be a string")

    raw = updated.encode("utf-8")
    if "\x00" in updated or len(raw) > MAX_EDIT_BYTES:
        raise ValueError("Edited contents  exceed the limit or contain NUL characters")

    preview = build_edit_preview(current["content"], updated)
    if not preview["diff"] or preview["truncated"] or preview["diff"] != proposal["diff"]:
        raise ValueError("Edit preview is unvalid; prepare a new edit")

    target = Path(current["path"])


    def file_state(info):
        return (
            info.st_dev, info.st_ino, info.st_mode,
            info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        )

    original_info = target.lstat()
    before = file_state(original_info)

    if not stat.S_ISREG(original_info.st_mode) or original_info.st_nlink != 1:
        raise  ValueError("Editing requires a regular file with one hard link")

    if (original_info.st_uid != os.geteuid() or original_info.st_mode & 0o7000 or os.listxattr(target)):
        raise ValueError("This file has ownership or metadata that this editor cannot preserve")

    if not os.access(target, os.W_OK):
        raise PermissionError("File is not writable")
    
    check_cancelled()
    if on_edit_approval(str(target), preview["diff"]) is not True:
        raise PermissionError("Edit denied. Nothing was written")
    check_cancelled()

    approved = resolve_workspace_path(
        proposal.get("access_path", str(target)),
        workspace_root,
        operation="write",
        on_file_approval=on_file_approval,
        on_sensitive_approval=on_sensitive_approval,
    )
    if approved != target:
        raise ValueError("File path changed; prepare a new edit")

    # Reuse read access granted earlier
    def approved_read(operation, path):
        return operation == "read" and path == str(target)

    def approved_sensitive_read(operation, path, reason):
        return operation == "read" and path == str(target)

    def recheck():
        check_cancelled()

        verify_edit_snapshot(
            proposal,
            workspace_root=workspace_root,
            on_file_approval=approved_read,
            on_sensitive_approval=approved_sensitive_read,
        )
        if file_state(target.lstat()) != before:
            raise ValueError("File changed. Prepare and review a new edit")

        check_cancelled()

    temporary = None
    try:
        recheck()

        with NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".oceanus-edit-",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(raw)
            output.flush()

            if os.fstat(output.fileno()).st_gid != original_info.st_gid:
                os.fchown(output.fileno(), -1, original_info.st_gid)

            os.fchmod(output.fileno(), stat.S_IMODE(original_info.st_mode))
            os.fsync(output.fileno())

        recheck()
        # Commit the edit
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return f"Edited {target}."


def edit_file(
        path: str,
        old_text: str,
        new_text: str,
        *,
        workspace_root: Path,
        on_file_approval: Callable[[str, str], bool] | None = None,
        on_edit_approval: Callable[[str, str], bool] | None = None,
        should_cancel: Callable[[], bool] | None =  None,
        on_sensitive_approval: Callable[[str, str, str], bool] | None = None,
) -> str:
    """ Prepare and apply one exact replacement with fresh diff approval """
    if should_cancel is not None and should_cancel():
        raise PermissionError("Edit cancelled. Nothing was written!")

    if on_edit_approval is None:
        raise PermissionError("Edit approval is required!")

    approved_sensitive_reads = set()

    def sensitive_approval(operation, resolved_path, reason):
        """ Remembers which sensitive reads have been approved by user """
        if should_cancel is not None and should_cancel():
            return False

        grant = (resolved_path, reason)
        if operation == "read" and grant in approved_sensitive_reads:
            return True

        if on_sensitive_approval is None:
            return False

        decision = on_sensitive_approval(operation, resolved_path, reason)
        if decision is not True:
            return False
        if should_cancel is not None and should_cancel():
            return False

        if operation == "read":
            approved_sensitive_reads.add(grant)
        return True

    proposal = prepare_edit(
        path,
        old_text,
        new_text,
        workspace_root=workspace_root,
        on_file_approval=on_file_approval,
        on_sensitive_approval=sensitive_approval
    )

    def file_approval(operation, resolved_path):
        if should_cancel is not None and should_cancel():
            return False

        # Exact path was already read with permission during preparation
        if operation == "read" and resolved_path == proposal["path"]:
            return True

        if on_file_approval is None:
            return False

        return on_file_approval(operation, resolved_path) is True

    return apply_edit(
        proposal,
        workspace_root=workspace_root,
        on_file_approval=file_approval,
        on_edit_approval=on_edit_approval,
        should_cancel=should_cancel,
        on_sensitive_approval=sensitive_approval
    )