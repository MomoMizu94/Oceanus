from textual.widgets import TextArea
from textual.events import Key
from textual.message import Message


class PromptEditor(TextArea):
    """ Edit a multiline prompt before sending """

    class Submitted(Message):
        """ Carry a copy of the prompt when enter is pressed """

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    DEFAULT_CSS = """
    PromptEditor {
        height: 7;
        min-height: 3;
        background: $surface;
        color: $foreground;
    }
    """

    def __init__(self, *, id: str | None = None, disabled: bool = False) -> None:
        super().__init__(
            id=id,
            disabled=disabled,
            theme="css",
            # Wrap visually without inserting extra newlines
            soft_wrap=True,
            # Let tab move focus to another control
            tab_behavior="focus",
            show_line_numbers=False,
            highlight_cursor_line=False,
            placeholder="Enter sends | Shift+Enter addds a new line",
        )
        self.border_title = "Prompt"

    def on_key(self, event: Key) -> None:
        if event.key not in ("enter", "shift+enter"):
            return

        # Prevent TextArea from handling these keys
        event.stop()
        event.prevent_default()

        if self.disabled or self.read_only:
            return

        if event.key == "shift+enter":
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
        elif self.text.strip():
            self.post_message(self.Submitted(self.text))