import codecs
import math
import os
import selectors
import signal
import subprocess
import time
from io import StringIO

from agent_core.output_store import MAX_OUTPUT_CHARS


class OutputBuffer:
    """ Keep a bounded text prefix while counting all decoded output """

    def __init__(self, max_chars: int = MAX_OUTPUT_CHARS) -> None:
        if type(max_chars) is not int or not 1 <= max_chars <= MAX_OUTPUT_CHARS:
            raise ValueError(f"max_chars must be between 1 and {MAX_OUTPUT_CHARS}")

        self._max_chars = max_chars
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._text = StringIO()
        self._stored_chars = 0
        self._original_chars = 0
        self._finished = False

    def _append(self, text: str) -> None:
        """ Counts every decoded character; only stores what fits """
        self._original_chars += len(text)
        remaining = self._max_chars - self._stored_chars
        retained = text[:remaining]
        self._text.write(retained)
        self._stored_chars += len(retained)

    def feed(self, data: bytes) -> None:
        """ Receives bytes in chunks """
        if self._finished:
            raise RuntimeError("Cannot feed output after finish()")
        self._append(self._decoder.decode(data))

    def finish(self) -> dict:
        """ Flush the decoder at EOF and return retained text with its counts """
        if not self._finished:
            self._append(self._decoder.decode(b"", final=True))
            self._finished = True

        return {
            "content": self._text.getvalue(),
            "stored_chars": self._stored_chars,
            "original_chars": self._original_chars,
            "truncated": self._stored_chars < self._original_chars
        }

def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        # The group already exited
        pass

def capture_shell(
        command: str,
        *,
        cwd: str,
        timeout: float = 60,
        max_chars: int = MAX_OUTPUT_CHARS
) -> dict:
    """ Collect bounded output from an already approved command on POSIX """
    if os.name != "posix":
        raise NotImplementedError("Shell capture currently requires POSIX")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positibe finite number")

    buffers = {name: OutputBuffer(max_chars) for name in ("stdout", "stderr")}
    timed_out = False
    capture_incomplete = False

    with selectors.DefaultSelector() as reader:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True
        )
        try:
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                os.set_blocking(pipe.fileno(), False)
                reader.register(pipe, selectors.EVENT_READ, data=name)

            deadline = time.monotonic() + timeout
            while reader.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if timed_out:
                        capture_incomplete = True
                        break
                    timed_out = True
                    _kill_process_group(process)
                    deadline = time.monotonic() + 1.0
                    continue

                for key, _ in reader.select(remaining):
                    try:
                        chunk = os.read(key.fd, 4096)
                    except BlockingIOError:
                        continue
                    if chunk:
                        buffers[key.data].feed(chunk)
                    else:
                        reader.unregister(key.fileobj)
                        key.fileobj.close()

            if not timed_out:
                try:
                    process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True

        finally:
            try:
                if process.returncode is None:
                    _kill_process_group(process)
                    process.wait(timeout=1.0)
            finally:
                process.stdout.close()
                process.stderr.close()

    return {
        "returncode": process.returncode,
        "timed_out": timed_out,
        "capture_incomplete": capture_incomplete,
        "stdout": buffers["stdout"].finish(),
        "stderr": buffers["stderr"].finish()
    }