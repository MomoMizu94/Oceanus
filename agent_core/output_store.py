import os
from pathlib import Path
from tempfile import NamedTemporaryFile


MAX_OUTPUT_CHARS = 1_000_000
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_FILES = 1000


class OutputStorageFullError(OSError):
    """ Saving another log would exceed storage budget -error """

def save_output_log(
        text: str,
        *,
        output_dir: Path | None = None,
        max_chars: int = MAX_OUTPUT_CHARS,
        max_total_bytes: int = MAX_OUTPUT_BYTES,
        max_files: int = MAX_OUTPUT_FILES) -> dict:
    """ Save a bounded UTF-8 log and report whether anything was omitted """
    """ Preserves longer output through restarts """

    # Validation
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_OUTPUT_CHARS:
        raise ValueError(f"max_chars musst be between 1 and {MAX_OUTPUT_CHARS}")

    for name, value in (("max_total_bytes", max_total_bytes), ("max_files", max_files)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    if os.name != "posix":
        raise OSError("Output log locking requires POSIX")
    import fcntl

    # Dir creation
    directory = output_dir if output_dir is not None else (
        Path.home() / ".local" / "share" / "oceanus" / "outputs"
    )
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Calculate how many bytes are needed
    stored = text[:max_chars]
    needed_bytes = len(stored.encode("utf-8"))

    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        used_bytes = 0
        file_count = 0
        # Count existing files and sizes; reject if budget exceeds
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    raise OSError("Output directory contains a non-regular file")

                used_bytes += entry.stat(follow_symlinks=False).st_size
                file_count += 1

                if used_bytes + needed_bytes > max_total_bytes or file_count >= max_files:
                    raise OutputStorageFullError("Output storage limit reached!")

        if needed_bytes > max_total_bytes:
            raise OutputStorageFullError("Output log exceeds strorage limits!")

        log_path = None
        try:
            # File creation - save as a log file
            with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", prefix="shell-", suffix=".txt", dir=directory, delete=False) as log:
                log_path = Path(log.name)
                log.write(stored)

        except (OSError, UnicodeError):
            if log_path is not None:
                log_path.unlink(missing_ok=True)
            raise
    finally:
        os.close(directory_fd)

    return {
        "path": str(log_path),
        "stored_chars": len(stored),
        "original_chars": len(text),
        "truncated": len(stored) < len(text)
    }