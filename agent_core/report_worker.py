""" Render one report in a process with resource limits """
import json
import sys

from agent_core.report_limits import (
    MAX_PDF_BYTES,
    MAX_REPORT_JOB_BYTES,
    REPORT_CPU_SECONDS,
    REPORT_MEMORY_BYTES,
    REPORT_EXIT_BLOCKED,
    REPORT_EXIT_FAILED,
    REPORT_EXIT_INVALID,
    REPORT_EXIT_LIMITS,
    REPORT_EXIT_MEMORY,
)


def _apply_report_limits() -> None:
    if not sys.platform.startswith("linux"):
        raise OSError("PDF worker limits require Linux")

    import resource

    limits = (
        (resource.RLIMIT_AS, REPORT_MEMORY_BYTES, REPORT_MEMORY_BYTES),
        (resource.RLIMIT_CPU, REPORT_CPU_SECONDS, REPORT_CPU_SECONDS + 1),
        (resource.RLIMIT_CORE, 0, 0),
    )
    for kind, soft, hard in limits:
        current_soft, current_hard = resource.getrlimit(kind)
        # Preserve any stricter limits inherited from the parent
        if current_hard != resource.RLIM_INFINITY:
            hard = min(hard, current_hard)
        soft = min(soft, hard)
        if current_soft != resource.RLIM_INFINITY:
            soft = min(soft, current_soft)
        resource.setrlimit(kind, (soft, hard))


def main() -> int:
    try:
        _apply_report_limits()
    except (OSError, ValueError):
        return REPORT_EXIT_LIMITS

    try:
        raw = sys.stdin.buffer.read(MAX_REPORT_JOB_BYTES + 1)
        if len(raw) > MAX_REPORT_JOB_BYTES:
            return REPORT_EXIT_INVALID
        job = json.loads(raw.decode("ascii"))
        if not isinstance(job, dict) or set(job) != {"markdown", "title"}:
            return REPORT_EXIT_INVALID
    except (ValueError, RecursionError):
        return REPORT_EXIT_INVALID
    except MemoryError:
        return REPORT_EXIT_MEMORY

    # Load native rendering libraries only after limits are active
    try:
        from weasyprint.urls import FatalURLFetchingError
        from agent_core.report_pdf import render_report_pdf
    except MemoryError:
        return REPORT_EXIT_MEMORY
    except Exception:
        return REPORT_EXIT_FAILED

    try:
        pdf = render_report_pdf(job["markdown"], title=job["title"])
        if not isinstance(pdf, bytes) or len(pdf) > MAX_PDF_BYTES:
            return REPORT_EXIT_FAILED
        if not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF"):
            return REPORT_EXIT_FAILED
        sys.stdout.buffer.write(pdf)
        sys.stdout.buffer.flush()
    except FatalURLFetchingError:
        return REPORT_EXIT_BLOCKED
    except ValueError:
        return REPORT_EXIT_INVALID
    except MemoryError:
        return REPORT_EXIT_MEMORY
    except Exception:
        # Exit codes keep exception details and report content out of diagnostics
        return REPORT_EXIT_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
