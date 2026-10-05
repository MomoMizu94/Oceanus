from pathlib import Path
from tempfile import NamedTemporaryFile


MAX_OUTPUT_CHARS = 1_000_000


def save_output_log(text: str, *, output_dir: Path | None = None, max_chars: int = MAX_OUTPUT_CHARS) -> dict:
    """ Save a bounded UTF-8 log and report whether anything was omitted """

    # Validation
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_OUTPUT_CHARS:
        raise ValueError(f"max_chars musst be between 1 and {MAX_OUTPUT_CHARS}")

    # Dir creation
    directory = output_dir if output_dir is not None else (
        Path.home() / ".local" / "share" / "oceanus" / "outputs"
    )
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stored = text[:max_chars]
    log_path = None

    try:
        # File creation
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", prefix="shell-", suffix=".txt", dir=directory, delete=False) as log:
            log_path = Path(log.name)
            log.write(stored)

    except (OSError, UnicodeError):
        if log_path is not None:
            log_path.unlink(missing_ok=True)
        raise

    return {
        "path": str(log_path),
        "stored_chars": len(stored),
        "original_chars": len(text),
        "truncated": len(stored) < len(text)
    }