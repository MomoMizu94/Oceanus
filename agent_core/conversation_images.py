"""Find local conversation images and load bounded thumbnails."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

from agent_core.guardrails import resolve_workspace_path
from agent_core.image_attachments import (
    MAX_IMAGES_PER_MESSAGE, _read_image_file, load_image_attachment, validate_image_bytes,
)
from agent_core.report_markdown import parse_report_markdown


IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
MAX_REFERENCE_TEXT = 64_000
MAX_THUMBNAIL_SIZE = (512, 384)
PATH_PATTERN = re.compile(r'''[^\s<>"'`()\[\]]+\.(?:png|jpe?g|webp)\b''', re.IGNORECASE)


@dataclass(frozen=True)
class ConversationImage:
    source: str
    label: str
    attachment: bool = False


def local_image_path(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            return None
        decoded = unquote(value, errors='strict')
    except (ValueError, UnicodeError):
        return None
    if any(ord(character) < 32 for character in decoded):
        return None
    if Path(decoded).suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return decoded


def conversation_images(text, *, attachments=None, kind='assistant'):
    references = []
    seen = set()

    def add_path(value):
        path = local_image_path(value)
        if path is not None and path not in seen and len(references) < MAX_IMAGES_PER_MESSAGE:
            seen.add(path)
            references.append(ConversationImage(path, Path(path).name))

    if isinstance(attachments, list):
        for index, attachment in enumerate(attachments[:MAX_IMAGES_PER_MESSAGE], start=1):
            if isinstance(attachment, dict) and isinstance(attachment.get('id'), str):
                identifier = attachment['id']
                if identifier not in seen:
                    seen.add(identifier)
                    label = attachment.get('label')
                    if not isinstance(label, str) or re.fullmatch(r'\[Image [1-9][0-9]*\]', label) is None:
                        label = f'Image {index}'
                    references.append(ConversationImage(identifier, label, attachment=True))

    if kind not in ('user', 'assistant', 'tool') or not isinstance(text, str) or not text.strip():
        return references
    text = text[:MAX_REFERENCE_TEXT]

    if kind == 'tool':
        # Saved tool messages wrap the original result in a status envelope
        payload = text
        for _ in range(3):
            try:
                decoded = json.loads(payload)
            except (ValueError, TypeError, RecursionError):
                break
            if not isinstance(decoded, dict):
                return references
            if decoded.get('status') in ('error', 'skipped', 'denied'):
                return references
            if 'path' in decoded:
                add_path(decoded['path'])
                return references
            payload = decoded.get('content')
            if not isinstance(payload, str):
                return references
        if isinstance(payload, str):
            text = payload
        if text.startswith(('Tool error:', 'Command denied:')):
            return references

    try:
        _, tokens, _ = parse_report_markdown(text, images=True)
    except ValueError:
        return references
    for token in tokens:
        # Ignore fenced code examples; scan displayed prose and inline paths only
        if token.type != 'inline':
            continue
        for child in token.children or []:
            if child.type == 'image':
                add_path(child.attrGet('src'))
            elif child.type == 'link_open':
                add_path(child.attrGet('href'))
            elif child.type in ('text', 'code_inline'):
                if child.type == 'code_inline':
                    add_path(child.content.strip())
                    if local_image_path(child.content.strip()) is not None:
                        continue
                for match in PATH_PATTERN.finditer(child.content):
                    add_path(match.group().lstrip('(['))
    return references


def load_conversation_thumbnail(reference, workspace, *, attachment_dir=None):
    thumbnail, dimensions, _ = load_changed_thumbnail(reference, workspace, None, attachment_dir=attachment_dir)
    return thumbnail, dimensions


def load_changed_thumbnail(reference, workspace, previous_digest, *, attachment_dir=None):
    if reference.attachment:
        stored = load_image_attachment(reference.source, attachment_dir=attachment_dir)
        raw = stored['data']
    else:
        # Displaying a model-supplied path must not grant new external file access
        try:
            target = resolve_workspace_path(reference.source, Path(workspace), operation='read')
        except RuntimeError as error:
            raise ValueError("Could not resolve the preview path") from error
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            raw = _read_image_file(Path(target.name), directory_fd=directory)
        finally:
            os.close(directory)

    digest = sha256(raw).digest()
    if digest == previous_digest:
        return None
    if not reference.attachment:
        validate_image_bytes(raw)
    with Image.open(BytesIO(raw)) as original:
        oriented = ImageOps.exif_transpose(original)
        dimensions = oriented.size
        oriented.thumbnail(MAX_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        thumbnail = oriented.convert('RGBA')
    return thumbnail, dimensions, digest
