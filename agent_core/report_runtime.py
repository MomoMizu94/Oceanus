""" Control one PDF worker without blocking cancellation on pipe IO """
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from agent_core.report_limits import (
    MAX_MARKDOWN_CHARS,
    MAX_PDF_BYTES,
    MAX_REPORT_JOB_BYTES,
    MAX_REPORT_TITLE_CHARS,
    REPORT_EXIT_BLOCKED,
    REPORT_EXIT_FAILED,
    REPORT_EXIT_INVALID,
    REPORT_EXIT_LIMITS,
    REPORT_EXIT_MEMORY,
    REPORT_POLL_INTERVAL,
    REPORT_TOTAL_TIMEOUT,
)


REPORT_WORKER_START = (
    "import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from agent_core.report_worker import main; "
    "sys.exit(main())"
)


def _encode_report_job(markdown: str, title: str) -> bytes:
    for value, limit, label in (
        (markdown, MAX_MARKDOWN_CHARS, "Markdown"),
        (title, MAX_REPORT_TITLE_CHARS, "title"),
    ):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"Report {label} must be text within its character limit!")
        if not value.strip() or "\x00" in value:
            raise ValueError(f"Report {label} must be nonblank and contain no NUL characters!")

    job = json.dumps({"markdown": markdown, "title": title}, ensure_ascii=True)
    encoded = job.encode("ascii")
    if len(encoded) > MAX_REPORT_JOB_BYTES:
        raise ValueError("PDF worker request is too large!")
    return encoded


def _check_report_stop(deadline: float, should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("PDF rendering cancelled.")
    if time.monotonic() >= deadline:
        raise TimeoutError("PDF rendering exceeded its overall time limit.")


def _check_report_exit(returncode: int) -> None:
    if returncode == 0:
        return
    if returncode == REPORT_EXIT_INVALID:
        raise ValueError("Report input was invalid or the rendered output exceeded its limit.")
    if returncode == REPORT_EXIT_BLOCKED:
        raise PermissionError("PDF rendering attempted to load a blocked resource.")
    if returncode == REPORT_EXIT_MEMORY:
        raise OSError("PDF worker exceeded its memory allowance.")
    if returncode == -signal.SIGXCPU:
        raise TimeoutError("PDF worker exceeded its CPU time limit.")
    if returncode == REPORT_EXIT_LIMITS:
        raise OSError("PDF worker could not apply its resource limits.")
    if returncode == REPORT_EXIT_FAILED:
        raise OSError("PDF worker failed to render the report.")
    raise OSError("PDF worker exited unexpectedly; no PDF was accepted.")


def _stop_report_worker(process: subprocess.Popen) -> None:
    # Also stop descendants that could keep a pipe open after the worker exits
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1.0)


def render_report_pdf_in_worker(
    markdown: str,
    *,
    title: str = "Oceanus report",
    should_cancel: Callable[[], bool] | None = None,
    timeout: float = REPORT_TOTAL_TIMEOUT,
) -> bytes:
    if not sys.platform.startswith("linux"):
        raise OSError("PDF worker control currently requires Linux!")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("PDF timeout must be a positive finite number!")

    deadline = time.monotonic() + timeout
    _check_report_stop(deadline, should_cancel)
    job = _encode_report_job(markdown, title)
    _check_report_stop(deadline, should_cancel)
    package_root = Path(__file__).resolve().parent.parent
    arguments = [sys.executable, "-I", "-c", REPORT_WORKER_START, str(package_root)]

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
            reply = bytearray()

            while selector.get_map() or process.poll() is None:
                _check_report_stop(deadline, should_cancel)
                wait = max(0.0, min(REPORT_POLL_INTERVAL, deadline - time.monotonic()))
                if not selector.get_map():
                    time.sleep(wait)
                    continue

                for key, _ in selector.select(wait):
                    if key.fileobj is process.stdin:
                        try:
                            sent += os.write(key.fd, job[sent:sent + 65_536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                            continue
                        if sent == len(job):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        allowance = MAX_PDF_BYTES + 1 - len(reply)
                        try:
                            chunk = os.read(key.fd, min(65_536, allowance))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                        else:
                            reply.extend(chunk)
                            if len(reply) > MAX_PDF_BYTES:
                                raise OSError("PDF worker reply is too large.")

            _check_report_stop(deadline, should_cancel)
            _check_report_exit(process.returncode)
            if sent != len(job):
                raise OSError("PDF worker did not receive the complete request.")
            pdf = bytes(reply)
            if not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF"):
                raise OSError("PDF worker returned an incomplete or invalid PDF.")
            return pdf
        finally:
            try:
                _stop_report_worker(process)
            finally:
                process.stdin.close()
                process.stdout.close()
