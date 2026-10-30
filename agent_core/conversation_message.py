from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Markdown, Collapsible


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

    ConversationMessage Markdown.message-body {
        padding: 0;
    }

    ConversationMessage Markdown MarkdownBlock > .strong {
        color: $text-accent;
        text-style: bold;
    }

    ConversationMessage.user {
        background: $surface;
        padding: 1 2;
    }

    ConversationMessage .message-heading {
        text-style: bold;
    }

    ConversationMessage.assistant {
        margin-top: 1;
    }

    ConversationMessage.assistant .message-heading {
        color: $text-accent;
    }

    ConversationMessage.tool,
    ConversationMessage.status {
        margin-bottom: 0;
    }

    ConversationMessage.tool .message-body {
        margin-left: 2;
        color: $foreground-muted;
    }

    ConversationMessage.status .message-heading,
    ConversationMessage.status .message-body,
    ConversationMessage.notice .message-heading,
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

    def __init__(self, kind: str, text: str, *, heading: str | None = None) -> None:
        if kind not in self.LABELS:
            raise ValueError(f"Unknown conversation message type: {kind}")

        # Class name lets CSS style this msg type
        super().__init__(classes=kind)
        self.text = text

        if heading is None:
            self.heading = self.LABELS[kind]
        else:
            self.heading = heading


    def compose(self) -> ComposeResult:
        yield Static(
            self.heading,
            classes="message-heading",
            markup=False,
        )

        if self.has_class("assistant"):
            yield Markdown(
                self.text,
                classes="message-body",
                open_links=False,
            )
        elif self.has_class("tool"):
            # Hide the output until the user opens its details
            with Collapsible(
                title="Details",
                collapsed=True,
                classes="tool-details",
            ):
                yield Static(
                    self.text,
                    classes="message-body",
                    markup=False,
                )
        else:
            # Keep other message types literal
            yield Static(
                self.text,
                classes="message-body",
                markup=False,
            )