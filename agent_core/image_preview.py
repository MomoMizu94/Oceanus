"""Small conversation thumbnails with terminal-native image rendering."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Collapsible, Static
from textual_image.widget import Image as TerminalImage

from agent_core.conversation_images import load_conversation_thumbnail


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
        self.load_started = False
        self.loaded = False
        self.failed = False
        self.image_widget = TerminalImage(classes='preview-image')

    def compose(self) -> ComposeResult:
        with Collapsible(title='Preview', collapsed=True):
            with Container(classes='preview-frame'):
                yield self.image_widget
            yield Static(f'{self.reference.label} · Loading preview…', markup=False, classes='preview-caption')

    def on_collapsible_expanded(self):
        if not self.load_started:
            self.load_started = True
            self.run_worker(self.load_preview())

    async def load_preview(self):
        loop = asyncio.get_running_loop()
        try:
            thumbnail, dimensions = await loop.run_in_executor(
                PREVIEW_EXECUTOR, load_conversation_thumbnail, self.reference, self.workspace,
            )
        except (OSError, ValueError, SyntaxError):
            if self.is_mounted:
                self.failed = True
                self.query_one('.preview-frame').display = False
                self.query_one('.preview-caption', Static).update(
                    f'{self.reference.label} · Preview unavailable (missing, restricted, or unsupported image)'
                )
            return
        if not self.is_mounted:
            thumbnail.close()
            return
        self.image_widget.image = thumbnail
        self.query_one('.preview-caption', Static).update(
            f'{self.reference.label} · {dimensions[0]} × {dimensions[1]}'
        )
        self.loaded = True

    def on_unmount(self):
        thumbnail = self.image_widget.image
        self.image_widget.image = None
        if thumbnail is not None:
            thumbnail.close()
