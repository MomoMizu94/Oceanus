"""Small conversation thumbnails with terminal-native image rendering."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.events import Click
from textual.widgets import Collapsible, Static
from textual_image.widget import Image as TerminalImage

from agent_core.conversation_images import load_changed_thumbnail


# Keep session replay from decoding many full-size pictures at the same time
PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='oceanus-preview')


class ImagePreview(Vertical):
    DEFAULT_CSS = """
    ImagePreview {
        width: 44;
        max-width: 100%;
        height: auto;
    }
    ImagePreview Collapsible {
        background: transparent;
        border: none;
        padding: 0;
    }
    ImagePreview Collapsible > Contents {
        padding: 0;
    }
    ImagePreview .preview-frame {
        width: 100%;
        height: 12;
    }
    ImagePreview .preview-image {
        width: auto;
        height: auto;
    }
    ImagePreview .preview-caption {
        height: auto;
        color: $text-muted;
    }
    """

    def __init__(self, reference, workspace: Path):
        super().__init__()
        self.reference = reference
        self.workspace = workspace
        self.loaded = False
        self.failed = False
        self.digest = None
        self.load_lock = asyncio.Lock()
        self.image_widget = TerminalImage(classes='preview-image')

    def compose(self) -> ComposeResult:
        with Collapsible(title='Preview', collapsed=False):
            with Container(classes='preview-frame'):
                yield self.image_widget
            yield Static(f'{self.reference.label} · Loading preview…', markup=False, classes='preview-caption')

    def on_mount(self):
        # Textual marks the widget as mounted after this handler returns
        self.call_later(self.refresh_thumbnail)

    def refresh_thumbnail(self):
        self.run_worker(self.load_preview())

    @on(Click, '.preview-frame, .preview-image')
    def hide_thumbnail(self):
        self.query_one(Collapsible).collapsed = True

    async def load_preview(self):
        # A later mention must check the file after any earlier read finishes
        async with self.load_lock:
            if self.is_mounted:
                await self._update_thumbnail()

    async def _update_thumbnail(self):
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                PREVIEW_EXECUTOR, load_changed_thumbnail, self.reference, self.workspace, self.digest,
            )
        except (OSError, ValueError, SyntaxError):
            if self.is_mounted:
                self.clear_thumbnail()
                self.failed = True
                self.query_one('.preview-frame').display = False
                self.query_one('.preview-caption', Static).update(
                    f'{self.reference.label} · Preview unavailable (missing, restricted, or unsupported image)'
                )
            return
        if result is None:
            return
        thumbnail, dimensions, digest = result
        if not self.is_mounted:
            thumbnail.close()
            return
        self.clear_thumbnail()
        self.image_widget.image = thumbnail
        self.digest = digest
        self.failed = False
        self.query_one('.preview-frame').display = True
        self.query_one('.preview-caption', Static).update(
            f'{self.reference.label} · {dimensions[0]} × {dimensions[1]}'
        )
        self.loaded = True

    def clear_thumbnail(self):
        self.loaded = False
        self.digest = None
        thumbnail = self.image_widget.image
        self.image_widget.image = None
        if thumbnail is not None:
            thumbnail.close()

    def on_unmount(self):
        self.clear_thumbnail()
