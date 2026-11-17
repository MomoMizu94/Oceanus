"""Run one page fetch for Oceanus's process controller"""
import base64
import json
import sys

from agent_core.tools.fetch import _request_fetch_page, _request_fetch_pdf, _extract_fetch_text
from agent_core.pdf_read_limits import MAX_PDF_INPUT_BYTES


MAX_FETCH_JOB_BYTES = 8192
MAX_FETCH_REPLY_BYTES = 262_144
MAX_FETCH_PDF_REPLY_BYTES = 4 * ((MAX_PDF_INPUT_BYTES + 2) // 3) + MAX_FETCH_JOB_BYTES + 1024

# Keep provider responses, page text, and internal paths out of error messages.
# Order matters: PermissionError and TimeoutError are also kinds of OSError.
FETCH_WORKER_ERRORS = {
    PermissionError: "Page destination was blocked by the access policy.",
    TimeoutError: "Page request timed out.",
    ValueError: "Page URL, response, or text encoding was invalid or unsupported.",
    OSError: "Could not retrieve the page.",
}

def _run_fetch_job(url: str) -> dict:
    """ Fetch and extract one page """

    try:
        # Download the page
        page = _request_fetch_page(url)
        # Extract the text
        extracted = _extract_fetch_text(page["body"], page["charset"], page["content_type"])
        return {
            "status": "success",
            "result": {
                "url": page["url"],
                "content_type": page["content_type"],
                "content": extracted["content"],
                "truncated": extracted["truncated"]
            }
        }
    except Exception as error:
        # Report failures as JSON. Unexpected errors remain failures but their
        # details are not copied into the conversation.
        error_type = "RuntimeError"
        message = "The page worker failed unexpectedly."
        for error_class, safe_message in FETCH_WORKER_ERRORS.items():
            if isinstance(error, error_class):
                error_type = error_class.__name__
                message = safe_message
                break
        return {
            "status": "error",
            "error_type": error_type,
            "message": message
        }

def _run_pdf_fetch_job(url: str) -> dict:
    try:
        document = _request_fetch_pdf(url)
        encoded = base64.b64encode(document["body"]).decode("ascii")
        return {
            "status": "success",
            "result": {"url": url, "content_type": "application/pdf", "body_base64": encoded},
        }
    except Exception as error:
        error_type = "RuntimeError"
        message = "PDF download worker failed."
        for error_class in FETCH_WORKER_ERRORS:
            if isinstance(error, error_class):
                error_type = error_class.__name__
                break
        return {"status": "error", "error_type": error_type, "message": message}


def main() -> None:
    """ Read JSON job from stdin and write one JSON reply to stdout """

    # The controller will close stdin after sending the job. Bound this read
    # even though the controller is expected to send only a short URL.
    raw = sys.stdin.buffer.read(MAX_FETCH_JOB_BYTES + 1)
    reply_limit = MAX_FETCH_REPLY_BYTES
    try:
        if len(raw) > MAX_FETCH_JOB_BYTES:
            raise ValueError("Job is too large.")
        job = json.loads(raw.decode("utf-8"))
        if not isinstance(job, dict) or set(job) not in ({"url"}, {"url", "kind"}):
            raise ValueError("Expected a URL job.")
        if "kind" in job and job["kind"] != "pdf":
            raise ValueError("Invalid fetch job kind.")
        if not isinstance(job["url"], str):
            raise ValueError("Expected a text URL.")
    except (ValueError, RecursionError):
        reply = {
            "status": "error",
            "error_type": "ValueError",
            "message": "Invalid page worker request.",
        }
    else:
        if job.get("kind") == "pdf":
            reply_limit = MAX_FETCH_PDF_REPLY_BYTES
            reply = _run_pdf_fetch_job(job["url"])
        else:
            reply = _run_fetch_job(job["url"])

    # ASCII JSON preserves Unicode through escapes and avoids terminal encoding
    # differences. Limit the reply too, before writing it to the parent process.
    output = json.dumps(reply, ensure_ascii=True).encode("ascii")
    if len(output) > reply_limit:
        reply = {
            "status": "error",
            "error_type": "RuntimeError",
            "message": "Page worker result exceeds the size limit.",
        }
        output = json.dumps(reply).encode("ascii")
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
