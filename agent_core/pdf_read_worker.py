""" Apply resource limits, then run a fixed Poppler command offline """
import os
import shutil
import sys

from agent_core.pdf_read_limits import (
    MAX_PDF_PAGES,
    PDF_EXIT_INVALID_JOB,
    PDF_EXIT_LIMITS,
    PDF_EXIT_UNAVAILABLE,
    PDF_READ_CPU_SECONDS,
    PDF_READ_MEMORY_BYTES,
)
from agent_core.shell_sandbox import (
    SYSTEM_PATH, ShellSandboxError, build_sandbox_base_args, build_shell_environment,
)


def apply_pdf_read_limits() -> None:
    if not sys.platform.startswith("linux"):
        raise OSError("PDF reading currently requires Linux")
    import resource

    limits = (
        (resource.RLIMIT_AS, PDF_READ_MEMORY_BYTES, PDF_READ_MEMORY_BYTES),
        (resource.RLIMIT_CPU, PDF_READ_CPU_SECONDS, PDF_READ_CPU_SECONDS + 1),
        (resource.RLIMIT_CORE, 0, 0),
    )
    for kind, soft, hard in limits:
        current_soft, current_hard = resource.getrlimit(kind)
        if current_hard != resource.RLIM_INFINITY:
            hard = min(hard, current_hard)
        soft = min(soft, hard)
        if current_soft != resource.RLIM_INFINITY:
            soft = min(soft, current_soft)
        resource.setrlimit(kind, (soft, hard))


def poppler_arguments(operation: str, page: int) -> list[str]:
    if operation not in ("info", "text") or type(page) is not int or not 1 <= page <= MAX_PDF_PAGES:
        raise ValueError("Invalid PDF worker operation or page")
    program_name = "pdfinfo" if operation == "info" else "pdftotext"
    program = shutil.which(program_name, path=SYSTEM_PATH)
    if program is None:
        raise FileNotFoundError("Poppler is required for PDF reading")
    if operation == "info":
        return [program, "-enc", "UTF-8", "-"]
    return [
        program, "-f", str(page), "-l", str(page),
        "-enc", "UTF-8", "-eol", "unix", "-nopgbrk", "-", "-",
    ]


def main(arguments: list[str]) -> int:
    try:
        if len(arguments) != 2:
            return PDF_EXIT_INVALID_JOB
        command = poppler_arguments(arguments[0], int(arguments[1]))
    except ValueError:
        return PDF_EXIT_INVALID_JOB
    except OSError:
        return PDF_EXIT_UNAVAILABLE
    try:
        apply_pdf_read_limits()
    except (OSError, ValueError):
        return PDF_EXIT_LIMITS
    try:
        # Pass only PDF bytes on stdin; mount no workspace or personal files
        launch = build_sandbox_base_args()
        launch.extend(["--", *command])
        os.execve(launch[0], launch, build_shell_environment())
    except (OSError, ShellSandboxError):
        return PDF_EXIT_UNAVAILABLE
    return PDF_EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
