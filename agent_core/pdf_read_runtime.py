""" Send one PDF snapshot to bounded, offline Poppler workers """
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from agent_core.pdf_read_limits import (
    MAX_PDF_INFO_BYTES, MAX_PDF_INPUT_BYTES, MAX_PDF_PAGE_BYTES, MAX_PDF_PAGES,
    PDF_EXIT_INVALID_JOB, PDF_EXIT_LIMITS, PDF_EXIT_UNAVAILABLE, PDF_READ_TIMEOUT,
)


PDF_READ_WORKER_START = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from agent_core.pdf_read_worker import main; sys.exit(main(sys.argv[2:]))"
)


def check_pdf_stop(deadline: float, should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("PDF reading cancelled.")
    if time.monotonic() >= deadline:
        raise TimeoutError("PDF reading exceeded its overall time allowance.")


def _check_pdf_exit(returncode: int) -> None:
    if returncode == 0:
        return
    if returncode == PDF_EXIT_UNAVAILABLE:
        raise OSError("PDF reading requires Poppler (pdfinfo and pdftotext) and Bubblewrap.")
    if returncode == PDF_EXIT_LIMITS:
        raise OSError("PDF reader could not apply its resource limits.")
    if returncode == PDF_EXIT_INVALID_JOB:
        raise ValueError("Invalid PDF reader job.")
    if returncode in (-signal.SIGXCPU, -signal.SIGKILL):
        raise OSError("PDF reader exceeded a resource limit or was stopped.")
    raise ValueError(
        "PDF could not be read. It may be malformed or password-protected, "
        "or the parser sandbox could not start."
    )


def _capture_pdf_command(
    data: bytes, operation: str, page: int, *, deadline: float,
    should_cancel: Callable[[], bool] | None,
) -> bytes:
    check_pdf_stop(deadline, should_cancel)
    maximum = MAX_PDF_INFO_BYTES if operation == "info" else MAX_PDF_PAGE_BYTES
    package_root = Path(__file__).resolve().parent.parent
    arguments = [
        sys.executable, "-I", "-c", PDF_READ_WORKER_START,
        str(package_root), operation, str(page),
    ]
    with selectors.DefaultSelector() as selector:
        process = subprocess.Popen(
            arguments, shell=False, cwd="/", env={},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, start_new_session=True,
        )
        try:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            sent = 0
            output = bytearray()
            while selector.get_map() or process.poll() is None:
                check_pdf_stop(deadline, should_cancel)
                wait = max(0.0, min(0.1, deadline - time.monotonic()))
                if not selector.get_map():
                    time.sleep(wait)
                    continue
                for key, _ in selector.select(wait):
                    if key.fileobj is process.stdin:
                        try:
                            sent += os.write(key.fd, data[sent:sent + 65_536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                            continue
                        if sent == len(data):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        allowance = min(65_536, maximum + 1 - len(output))
                        try:
                            chunk = os.read(key.fd, allowance)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                        else:
                            output.extend(chunk)
                            if len(output) > maximum:
                                raise ValueError("PDF metadata or selected page text exceeds the extraction limit.")
            check_pdf_stop(deadline, should_cancel)
            _check_pdf_exit(process.returncode)
            if sent != len(data):
                raise OSError("PDF reader did not receive the complete input.")
            return bytes(output)
        finally:
            try:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1.0)
            finally:
                process.stdin.close()
                process.stdout.close()


def parse_pdf_info(raw: bytes) -> int:
    text = raw.decode("utf-8")
    page_counts = re.findall(r"^Pages:[ \t]*([0-9]+)[ \t]*$", text, re.MULTILINE)
    encryption = re.findall(r"^Encrypted:[ \t]*(.*)$", text, re.MULTILINE)
    if len(page_counts) != 1 or len(encryption) != 1:
        raise ValueError("PDF reader returned missing or ambiguous document metadata.")
    if encryption[0].strip() != "no":
        raise ValueError("Encrypted PDFs are not supported by the PDF reader.")
    count = int(page_counts[0])
    if not 1 <= count <= MAX_PDF_PAGES:
        raise ValueError(f"PDF must contain between 1 and {MAX_PDF_PAGES} pages.")
    return count


def extract_pdf_page(
    data: bytes, page: int, *, should_cancel: Callable[[], bool] | None = None,
    timeout: float = PDF_READ_TIMEOUT,
) -> tuple[int, str]:
    if not sys.platform.startswith("linux"):
        raise OSError("PDF reading currently requires Linux.")
    if not isinstance(data, bytes) or not data.startswith(b"%PDF-"):
        raise ValueError("The file must start with a PDF header.")
    if len(data) > MAX_PDF_INPUT_BYTES:
        raise ValueError("PDF exceeds the 10 MiB input limit.")
    if type(page) is not int or not 1 <= page <= MAX_PDF_PAGES:
        raise ValueError(f"PDF page must be an integer between 1 and {MAX_PDF_PAGES}.")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("PDF timeout must be positive and finite.")
    deadline = time.monotonic() + timeout
    metadata = _capture_pdf_command(data, "info", page, deadline=deadline, should_cancel=should_cancel)
    page_count = parse_pdf_info(metadata)
    if page > page_count:
        raise ValueError(f"PDF has {page_count} pages; page {page} does not exist.")
    raw_text = _capture_pdf_command(data, "text", page, deadline=deadline, should_cancel=should_cancel)
    return page_count, raw_text.decode("utf-8")
