from textual.app import ComposeResult
from textual.markup import escape
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, Markdown, Collapsible
from pathlib import Path

from agent_core.conversation_images import conversation_images, local_image_path
from agent_core.conversation_links import LiteralMessageBody, conversation_markdown_parser, is_web_url
from agent_core.image_preview import ImagePreview


class ConversationMessage(Vertical):
    """ Display one conversation entry with a heading and body """

    LABELS = {
        "user": "You",
        "assistant": "Agent",
        "tool": "Tool result",
        "status": "Status",
        "notice": "Notice",
        "error": "Error",
    }

    DEFAULT_CSS = """
    ConversationMessage {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        border: none;
        color: $foreground;
    }

    ConversationMessage .speaker-line { height: auto; }
    ConversationMessage .speaker-line > .message-heading {
        width: 2; height: 1; color: $text-accent;
    }
    ConversationMessage .speaker-line > .message-body { width: 1fr; }
    ConversationMessage .tool-details CollapsibleTitle { color: $text-muted; }
    ConversationMessage .tool-details CollapsibleTitle:focus { color: $text-accent; }

    ConversationMessage Markdown.message-body {
        padding: 0;
    }

    ConversationMessage Markdown MarkdownBlock > .strong {
        color: $text-accent;
        text-style: bold;
    }

    ConversationMessage.user {
        background: $surface;
        padding: 0 1;
    }

    ConversationMessage .message-heading {
        text-style: bold;
        margin-bottom: 0;
    }

    ConversationMessage.user,
    ConversationMessage.assistant {
        margin: 1 0;
    }

    ConversationMessage.assistant .message-heading {
        color: $text-accent;
    }

    ConversationMessage.status .message-body,
    ConversationMessage.notice .message-body,
    ConversationMessage.error .message-body {
        margin-left: 2;
    }

    ConversationMessage.tool .message-body {
        margin-left: 2;
        color: $foreground-muted;
    }

    ConversationMessage.status .message-body,
    ConversationMessage.notice .message-body {
        color: $foreground-muted;
    }

    ConversationMessage.error .message-heading {
        color: $text-error;
    }

    ConversationMessage.error .message-body {
        color: $foreground-muted;
    }

    ConversationMessage .tool-details {
        background: transparent;
        border: none;
        padding: 0;
    }

    ConversationMessage .tool-details > Contents {
        padding: 0;
    }

    """

    def __init__(self, kind: str, text: str, *, heading: str | None = None,
                 attachments=None, workspace: Path | None = None,
                 request_previews: dict[Path, ImagePreview] | None = None) -> None:
        if kind not in self.LABELS:
            raise ValueError(f"Unknown conversation message type: {kind}")

        # Class name lets CSS style this msg type
        super().__init__(classes=kind)
        self.text = text
        self.workspace = Path.cwd() if workspace is None else Path(workspace)
        self.previews: list[ImagePreview] = []
        self.image_links: dict[Path, ImagePreview] = {}
        if request_previews is None:
            request_previews = {}
        for reference in conversation_images(text, attachments=attachments, kind=kind):
            if reference.attachment:
                self.previews.append(ImagePreview(reference, self.workspace))
                continue

            # Tool results may use an absolute path while the reply links to a relative one
            path = self.workspace / reference.source
            preview = request_previews.get(path)
            if preview is None:
                preview = ImagePreview(reference, self.workspace)
                request_previews[path] = preview
                self.previews.append(preview)
            elif preview.is_mounted:
                preview.refresh_thumbnail()
            self.image_links[path] = preview

        if heading is None:
            self.heading = self.LABELS[kind]
        else:
            self.heading = heading


    def compose(self) -> ComposeResult:
        if self.has_class("tool"):
            with Collapsible(title=escape(self.heading), collapsed=True, classes="tool-details"):
                yield LiteralMessageBody(self.text)
        elif self.heading in ("You", "Agent", "Status", "Notice"):
            marker = "›" if self.has_class("user") else "•"
            with Horizontal(classes="speaker-line"):
                yield Static(marker, classes="message-heading", markup=False)
                yield self.body_widget()
        else:
            yield Static(self.heading, classes="message-heading", markup=False)
            yield self.body_widget()
        yield from self.previews

    def body_widget(self):
        if self.has_class("assistant"):
            return Markdown(
                self.text, classes="message-body",
                parser_factory=conversation_markdown_parser, open_links=False,
            )
        return LiteralMessageBody(self.text)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        if is_web_url(event.href):
            self.app.open_url(event.href)
            return
        source = local_image_path(event.href)
        if source is None:
            return
        preview = self.image_links.get(self.workspace / source)
        if preview is not None:
            preview.query_one(Collapsible).collapsed = False
            preview.call_after_refresh(preview.scroll_visible, animate=False)
