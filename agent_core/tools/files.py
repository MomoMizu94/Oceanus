# File-related tools
from pathlib import Path

def read_file(path: str) -> str:
    """ Read UTF-8 text and return contents """
    return Path(path).read_text(encoding="utf-8")