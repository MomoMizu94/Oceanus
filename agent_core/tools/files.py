# File-related tools
from pathlib import Path

def read_file(path: str) -> str:
    """ Read UTF-8 text and return contents """
    return Path(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    """ Create or overwrite a UTF-8 file """
    file_path = Path(path)
    text_written = file_path.write_text(
        content,
        encoding="utf-8"
    )
    return f"Wrote {text_written} into {file_path}."