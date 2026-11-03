import re
from io import BytesIO
import warnings
import fcntl
import os
import stat
from tempfile import NamedTemporaryFile
from pathlib import Path
import base64

from PIL import Image


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_ATTACHMENT_STORAGE_BYTES = 512 * 1024 * 1024
MAX_ATTACHMENT_FILES = 1000
MAX_IMAGES_PER_MESSAGE = 4

IMAGE_MEDIA_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp"
}


def validate_image_bytes(data: bytes) -> dict:
    """ Check an image and return its media type and dimensions """

    if not isinstance(data, bytes) or not data:
        raise ValueError("Image must be nonempty bytes!")

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 8 MiB full-size limit!")

    formats = list(IMAGE_MEDIA_TYPES)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            with Image.open(BytesIO(data), formats=formats) as picture:
                width, height = picture.size

                # Check dimensions before allocation
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image exceeds the pixel limit!")

                if getattr(picture, "n_frames", 1) != 1:
                    raise ValueError("Animated images are not supported!")

                media_type = IMAGE_MEDIA_TYPES[picture.format]
                picture.verify()

            # Reopen after verification to check pixel decoding
            with Image.open(BytesIO(data), formats=formats) as picture:
                picture.load()

    except (OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ValueError("Image is invalid, unsupported or can't be decoded!")

    return {
        "media_type": media_type,
        "width": width,
        "height": height
    }

def _read_image_file(path: Path, *, directory_fd: int | None = None) -> bytes:
    # Nonblocking mode allows reject pipes without waiting for a writer
    flags = os.O_RDONLY | os.O_NONBLOCK
    if directory_fd is not None:
        # Stored copies must not redirect reads through a symlink
        flags |= os.O_NOFOLLOW

    descriptor = os.open(path, flags, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Attach a regular image file")

        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(MAX_IMAGE_BYTES + 1)
    finally:
        os.close(descriptor)

    return data

def save_image_attachment(path: str | Path, *, attachment_dir: Path | None = None) -> dict:
    """ Read a selected file and store its image bytes """
    source_path = Path(path).expanduser()
    data = _read_image_file(source_path)

    result = save_image_bytes(data, attachment_dir=attachment_dir)
    result["name"] = source_path.name
    return result

def save_image_bytes(data: bytes, *, attachment_dir: Path | None = None) -> dict:
    """Validate image bytes and keep a private copy"""
    metadata = validate_image_bytes(data)

    if attachment_dir is None:
        directory = Path.home() / ".local/share/oceanus/sessions/attachments"
    else:
        directory = Path(attachment_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(directory, flags)
    stored_path = None

    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("Attachment directory must be private to your user!")

        # Keep 2 Oceanus processes from spending the same free space
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        used_bytes = 0
        file_count = 0
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    raise OSError("Attachment storage contains a non-regular file!")
                used_bytes += entry.stat(follow_symlinks=False).st_size
                file_count += 1
                if file_count >= MAX_ATTACHMENT_FILES:
                    raise OSError("Attachment limit reached!")
                if used_bytes + len(data) > MAX_ATTACHMENT_STORAGE_BYTES:
                    raise OSError("Attachment storage is full!")

        if len(data) > MAX_ATTACHMENT_STORAGE_BYTES:
            raise OSError("Image exceeds the attachment storage limit!")

        suffix = "." + metadata["media_type"].split("/")[1]

        # Temp file creation gives a unique name and owner-only access
        with NamedTemporaryFile(mode="wb", prefix="image-", suffix=suffix, dir=directory, delete=False) as stored:
            stored_path = Path(stored.name)
            stored.write(data)
    except OSError:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise
    finally:
        os.close(directory_fd)

    result = {
        "id": stored_path.name,
        "name": "clipboard" + suffix,
        "size_bytes": len(data)
    }
    result.update(metadata)
    return result

def load_image_attachment(attachment_id: str, *, attachment_dir: Path | None = None) -> dict:
    """ Read and validate a copy from attachment storage """
    if not isinstance(attachment_id, str) or len(attachment_id) > 100:
        raise ValueError("Invalid attachment ID!")

    pattern = r"image-[a-z0-9_]+\.(png|jpeg|webp)"
    if re.fullmatch(pattern, attachment_id) is None:
        raise ValueError("Attachment ID is invalid!")

    if attachment_dir is None:
        directory = Path.home() / ".local/share/oceanus/sessions/attachments"
    else:
        directory = Path(attachment_dir)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(directory, flags)
    try:
        info = os.fstat(directory_fd)
        other_permissions = stat.S_IMODE(info.st_mode) & 0o077
        if info.st_uid != os.getuid() or other_permissions:
            raise PermissionError("Attachment directory must be private to your user!")

        # Open the ID inside this dir, without resolving a new parent path
        data = _read_image_file(Path(attachment_id), directory_fd=directory_fd)
    finally:
        os.close(directory_fd)

    metadata = validate_image_bytes(data)
    result = {"data": data}
    result.update(metadata)
    return result

def build_user_content(text: str, attachment_ids: list[str], *, attachment_dir: Path | None = None) -> str | list[dict]:
    """ Build text and image content for a model request """
    if not isinstance(text, str):
        raise ValueError("Prompt text must be a string!")
    if not isinstance(attachment_ids, list):
        raise ValueError("Attachment IDs must be a list!")
    if len(attachment_ids) > MAX_IMAGES_PER_MESSAGE:
        raise ValueError(f"A message supports at most {MAX_IMAGES_PER_MESSAGE} images!")

    # Keep ordinary prompts in string format
    if not attachment_ids:
        return text

    content = []
    if text:
        content.append({"type": "text", "text": text})

    for attachment_id in attachment_ids:
        image = load_image_attachment(attachment_id, attachment_dir=attachment_dir)
        encoded_bytes = base64.b64encode(image["data"])
        encoded_text = encoded_bytes.decode("ascii")
        media_type = image["media_type"]

        # Data URL carries the image bytes inside the request
        data_url = f"data:{media_type};base64,{encoded_text}"
        image_part = {
            "type": "image_url",
            "image_url": {"url": data_url}
        }
        content.append(image_part)

    return content