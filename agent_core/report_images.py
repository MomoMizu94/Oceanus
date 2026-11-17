""" Snapshot approved local images for offline PDF rendering """
import base64
import hashlib
import os
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit

from agent_core.guardrails import resolve_workspace_path
from agent_core.image_attachments import MAX_IMAGE_BYTES, validate_image_bytes
from agent_core.report_limits import (
    MAX_REPORT_IMAGES, MAX_REPORT_IMAGE_BYTES, MAX_REPORT_IMAGE_PIXELS, MAX_REPORT_IMAGE_PATH_CHARS,
)
from agent_core.report_markdown import image_sources


def collect_report_images(markdown, source_path, *, workspace_root, on_file_approval=None,
                          on_sensitive_approval=None, should_cancel=None):
    def check_cancelled():
        if should_cancel is not None and should_cancel():
            raise InterruptedError('Report image loading cancelled.')

    check_cancelled()
    references = image_sources(markdown)
    if len(references) > MAX_REPORT_IMAGES:
        raise ValueError('Report supports at most 16 distinct image references.')
    images = {}
    metadata = []
    total = 0
    for reference in references:
        check_cancelled()
        if not reference or len(reference) > MAX_REPORT_IMAGE_PATH_CHARS:
            raise ValueError('Report image path must contain 1 to 2048 characters.')
        parts = urlsplit(reference)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise ValueError('Report images must use local paths without URL schemes, queries, or fragments.')
        decoded = unquote(reference, errors='strict')
        if '\x00' in decoded:
            raise ValueError('Report image path must not contain NUL characters.')
        requested = Path(decoded).expanduser()
        if not requested.is_absolute():
            requested = Path(source_path).parent / requested
        target = resolve_workspace_path(str(requested), workspace_root, operation='read',
                                        on_file_approval=on_file_approval,
                                        on_sensitive_approval=on_sensitive_approval)
        check_cancelled()
        descriptor = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError('Report images must be regular files.')
            with os.fdopen(descriptor, 'rb', closefd=False) as source:
                raw = source.read(MAX_IMAGE_BYTES + 1)
        finally:
            os.close(descriptor)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('Each report image must be nonempty and at most 8 MiB.')
        total += len(raw)
        if total > MAX_REPORT_IMAGE_BYTES:
            raise ValueError('Report images exceed the combined 16 MiB limit.')
        check_cancelled()
        images[reference] = base64.b64encode(raw).decode('ascii')
        metadata.append({'reference': reference, 'path': str(target),
                         'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    return images, metadata


def decode_report_images(images):
    # Tool exports validate pixels inside the resource-limited worker
    if not isinstance(images, dict) or len(images) > MAX_REPORT_IMAGES:
        raise ValueError('Invalid report image collection.')
    resources = {}
    urls = {}
    total_bytes = total_pixels = 0
    for reference, encoded in images.items():
        if not isinstance(reference, str) or not 1 <= len(reference) <= MAX_REPORT_IMAGE_PATH_CHARS:
            raise ValueError('Invalid report image reference.')
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError('Invalid or oversized encoded report image.')
        raw = base64.b64decode(encoded, validate=True)
        total_bytes += len(raw)
        if total_bytes > MAX_REPORT_IMAGE_BYTES:
            raise ValueError('Report images exceed the combined 16 MiB limit.')
        metadata = validate_image_bytes(raw)
        total_pixels += metadata['width'] * metadata['height']
        if total_pixels > MAX_REPORT_IMAGE_PIXELS:
            raise ValueError('Report images exceed the combined 40 million pixel limit.')
        url = f'https://oceanus.invalid/report-images/{len(resources)}'
        urls[reference] = url
        resources[url] = (raw, metadata['media_type'])
    return urls, resources
