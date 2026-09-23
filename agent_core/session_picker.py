from pathlib import Path
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static


class SessionPicker(ModalScreen[Path | None]):
    """ Return selected session path, or None when cancelled """

    BINDINGS = [
        ("escape", "cancel", "Cancel")
    ]

    DEFAULT_CSS = """
    SessionPicker {
        align: center middle;
    }

    #session-dialog {
        width: 90%;
        max-width: 90;
        height: 70%;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }

    #session-list {
        height: 1fr;
    }
    """

    def __init__(self, paths: list[Path]) -> None:
        """ Session screen initialization """
        super().__init__()
        self.paths = paths

    def compose(self) -> ComposeResult:
        with Vertical(id="session-dialog"):
            yield Static("Open session, newest first")
            yield OptionList(
                *(Text(path.name) for path in self.paths),
                id="session-list"
            )
            yield Static("Arrow keys: select | Enter: open | Escape: cancel")

    def on_mount(self) -> None:
        options = self.query_one("#session-list", OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.paths[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)