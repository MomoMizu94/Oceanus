from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class FileApprovalScreen(ModalScreen[bool]):
    """ Ask permission for external file operation """

    BINDINGS = [
        ("y, Y", "approve", "Approve"),
        ("n, N", "deny", "Deny"),
        ("escape", "deny", "Deny")
    ]

    # Style
    DEFAULT_CSS = """
    FileApprovalScreen {
        align: center middle;
    }

    #file-dialog {
        width: 90%;
        max-width: 90;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #file-details {
        height: 1fr;
        margin: 1 0;
    }

    #file-buttons {
        height: auto;
        align-horizontal: right;
    }

    #file-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, operation: str, path: str) -> None:
        """ Approval screen initialization """
        super().__init__()
        if operation not in ("read", "write"):
            raise ValueError("Expected a read or write operation")
        self.operation = operation
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="file-dialog"):
            yield Static("Allow this external file operation?")
            with VerticalScroll(id="file-details"):
                yield Static(
                    f"Operation: {self.operation}\nResolved path:\n{self.path}",
                    id="file-info",
                    markup=False
                )
                if self.operation == "write":
                    yield Static("This may create a file or overwrite existing file's contents.")
            with Horizontal(id="file-buttons"):
                yield Button("Deny", id="deny")
                yield Button("Approve", id="approve", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_approve(self) -> None:
        """ On approval, dismiss dialog and return True """
        self.dismiss(True)

    def action_deny(self) -> None:
        """ On denial, dismiss dialog and return False """
        self.dismiss(False)