import os
import selectors
import subprocess
import time

from agent_core.image_attachments import MAX_IMAGE_BYTES, validate_image_bytes


CLIPBOARD_TIMEOUT = 5.0
MAX_CLIPBOARD_TYPES_BYTES = 16 * 1024
CLIPBOARD_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")


def _read_clipboard_output(arguments: list[str], max_bytes: int) -> bytes:
    """ Read clipboard command with a size limit and deadline """
    deadline = time.monotonic() + CLIPBOARD_TIMEOUT

    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0
        )
    except FileNotFoundError:
        raise OSError("Image paste requires wl-paste from wl_clipboard") from None

    try:
        with selectors.DefaultSelector() as reader:
            os.set_blocking(process.stdout.fileno(), False)
            reader.register(process.stdout, selectors.EVENT_READ)
            output = bytearray()

            while reader.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise TimeoutError("Clipboard read timed out")

                for key, _ in reader.select(remaining_time):
                    # One extra byte tells whether the limit was exceeded
                    allowance = max_bytes + 1 - len(output)

                    try:
                        chunk = os.read(key.fd, min(65536, allowance))
                    except BlockingIOError:
                        continue

                    if not chunk:
                        reader.unregister(process.stdout)
                        break

                    output.extend(chunk)
                    if len(output) > max_bytes:
                        raise ValueError("Clipboard data exceeds the size limit")

            # A command can close its output before it exits
            remaining_time = max(0.0, deadline - time.monotonic())

            try:
                exit_code = process.wait(timeout=remaining_time)
            except subprocess.TimeoutExpired:
                raise TimeoutError("Clipboard read timed out") from None

            if exit_code != 0:
                raise OSError("Could not read the Wayland clipboard")

            return bytes(output)
    finally:
        # Stop unfinished reads and collect the child process
        if process.poll() is None:
            process.kill()

        process.wait()
        process.stdout.close()


def read_clipboard_image() -> bytes | None:
    """ Return a validated image, or None if no image """
    raw_types = _read_clipboard_output(["wl-paste", "--list-types"], MAX_CLIPBOARD_TYPES_BYTES)
    offered_types = raw_types.decode("utf-8").splitlines()

    for media_type in CLIPBOARD_IMAGE_TYPES:
        if media_type not in offered_types:
            continue

        data = _read_clipboard_output(["wl-paste", "--no-newline", "--type", media_type], MAX_IMAGE_BYTES)

        # Check bytes
        validate_image_bytes(data)
        return data

    for media_type in offered_types:
        if media_type.startswith("image/"):
            raise ValueError("Clipboard image must be a PNG, JPEG, or WebP image!")

    return None