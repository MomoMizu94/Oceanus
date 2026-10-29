""" Page-fetch worker controller on Linux """
import json
import math
import os
import selectors
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from agent_core.fetch_worker import (
    MAX_FETCH_JOB_BYTES,
    MAX_FETCH_REPLY_BYTES,
    FETCH_WORKER_ERRORS,
)
from agent_core.tools.fetch import MAX_FETCH_TEXT_CHARS


FETCH_TOTAL_TIMEOUT = 60.0
FETCH_POLL_INTERVAL = 0.1

# Import Oceanus from its own installation (the URL travels through stdin)
FETCH_WORKER_START = (
    "import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from agent_core.fetch_worker import main; "
    "main()"
)


def _check_fetch_stop(deadline: float, should_cancel: Callable[[], bool] | None = None) -> None:
    """ Stop before accepting more aork when cancellation or timeout occurs """
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Page request cancelled.")
    if time.monotonic() >= deadline:
        raise TimeoutError("Page request exceeded its overall time limit.")


def _collect_fetch_reply(
    url: str, 
    *, 
    should_cancel: Callable[[], bool] | None = None, 
    timeout: float = FETCH_TOTAL_TIMEOUT
) -> bytes:
    """ Collect one bounded worker reply after the caller obtains approval """
    if os.name != "posix":
        raise OSError("Page worker control currently requires POSIX!")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Page timeout must be a positive finite number!")
    if not isinstance(url, str):
        raise ValueError("Page URL must be a string object!")

    # Reject oversized input before creating any JSON representation
    if len(url) > MAX_FETCH_JOB_BYTES:
        raise ValueError("Page worker request is too large!")
    job = json.dumps({"url": url}, ensure_ascii=True).encode("ascii")
    if len(job) > MAX_FETCH_JOB_BYTES:
        raise ValueError("Page worker request is too large!")

    deadline = time.monotonic() + timeout
    _check_fetch_stop(deadline, should_cancel)
    package_root = Path(__file__).resolve().parent.parent
    arguments = [
        sys.executable, "-I", "-c", FETCH_WORKER_START, str(package_root)
    ]

    with selectors.DefaultSelector() as selector:
        process = subprocess.Popen(
            arguments, shell=False, cwd="/", env={},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True,
        )
        try:
            # Nonblocking pipes keep a stuck worker from trapping this loop
            # inside a read or write. The selector tells us which pipe is ready.
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            sent = 0
            reply = bytearray()

            while selector.get_map() or process.poll() is None:
                _check_fetch_stop(deadline, should_cancel)
                wait = min(FETCH_POLL_INTERVAL, deadline - time.monotonic())

                # The worker might close its pipes but keep running.
                # Still check its exit, cancellation, and deadline in that case.
                if not selector.get_map():
                    time.sleep(max(0.0, wait))
                    continue

                for key, _ in selector.select(max(0.0, wait)):
                    if key.fileobj is process.stdin:
                        try:
                            sent += os.write(key.fd, job[sent:])
                        except BlockingIOError:
                            continue

                        # Closing stdin tells the worker that the job is complete.
                        if sent == len(job):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        # Read at most one byte beyond the remaining allowance.
                        # That extra byte detects a reply that is too large.
                        allowance = MAX_FETCH_REPLY_BYTES + 1 - len(reply)
                        try:
                            chunk = os.read(key.fd, min(4096, allowance))
                        except BlockingIOError:
                            continue

                        if not chunk:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                        else:
                            reply.extend(chunk)
                            if len(reply) > MAX_FETCH_REPLY_BYTES:
                                raise OSError("Page worker reply is too large.")

            # Cancellation takes priority even if the worker just finished.
            _check_fetch_stop(deadline, should_cancel)
            if process.returncode != 0:
                raise OSError("Page worker exited unsuccessfully.")
            return bytes(reply)
        finally:
            # Every exit path closes the pipes and stops an unfinished worker.
            # wait() collects its exit status so it does not remain a zombie.
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1.0)
            finally:
                process.stdin.close()
                process.stdout.close()


def _parse_fetch_reply(raw: bytes, url: str) -> dict:
    """ Validate a worker reply and return the page or raise its error """
    if not isinstance(raw, bytes) or len(raw) > MAX_FETCH_REPLY_BYTES:
        raise ValueError("Invalid page worker reply!")
    if not isinstance(url, str):
        raise ValueError("Expected a page URL!")

    # The worker sends ASCII JSON including escapes for unicode text
    try:
        reply = json.loads(raw.decode("ascii"))
    except (ValueError, RecursionError):
        raise ValueError("Invalid page worker reply!") from None
    if not isinstance(reply, dict):
        raise ValueError("Invalid page worker reply!")

    # Use fixed local messages instead of echoing worker error deets
    if reply.get("status") == "error":
        if set(reply) != {"status", "error_type", "message"}:
            raise ValueError("Invalid page worker error!")
        if not isinstance(reply["message"], str):
            raise ValueError("Invalid page worker error!")

        for error_class, message in FETCH_WORKER_ERRORS.items():
            if reply["error_type"] == error_class.__name__:
                raise error_class(message)

        if reply["error_type"] == "RuntimeError":
            raise OSError("The page worker failed unexpectedly!")
        raise ValueError("Unknown page worker error type!")

    if reply.get("status") != "success" or set(reply) != {"status", "result"}:
        raise ValueError("Invalid page worker reply!")
    result = reply["result"]
    if not isinstance(result, dict):
        raise ValueError("Invalid page worker result!")
    # Checks the dictionary's field names
    if set(result) != {"url", "content_type", "content", "truncated"}:
        raise ValueError("Invalid page worker result!")

    # Keep the exact requested URL as the source
    if result["url"] != url:
        raise ValueError("Page worker returned a different URL!")
    if result["content_type"] not in ("text/html", "text/plain"):
        raise ValueError("Invalid page content type!")

    content = result["content"]
    if not isinstance(content, str) or len(content) > MAX_FETCH_TEXT_CHARS:
        raise ValueError("Invalid page text!")
    if type(result["truncated"]) is not bool:
        raise ValueError("Invalid page truncation flag!")

    # Reject controls and lone surrogates before display or serialization
    for character in content:
        number = ord(character)
        if character in ("\t", "\n", "\r"):
            continue
        if number < 32 or 127 <= number <= 159 or 0xD800 <= number <= 0xDFFF:
            raise ValueError("Invalid page text!")

    return result
