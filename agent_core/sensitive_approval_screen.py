from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from agent_core.sensitive_approval import describe_sensitive_access


class SensitiveApprovalScreen(ModalScreen[bool]):
    """ Ask permission for one sensitive filesys operation """

    BINDINGS = [
        ("y, Y", "approve", "Approve"),
        ("n, N", "deny", "Deny"),
        ("escape", "deny", "Deny")
    ]

    # Style
    DEFAULT_CSS = """
    SensitiveApprovalScreen {
        align: center middle;
    }

    #sensitive-dialog {
        width: 90%;
        max-width: 90;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #sensitive-details {
        height: 1fr;
        margin: 1 0;
    }

    #sensitive-buttons {
        height: auto;
        align-horizontal: right;
    }

    #sensitive-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, operation: str, path: str, reason: str) -> None:
        """ Approval screen initialization """
        super().__init__()
        # Build explanation
        self.details = describe_sensitive_access(operation, path, reason)

    def compose(self) -> ComposeResult:
        """ Explanation -> scrollable area with buttons below """
        with Vertical(id="sensitive-dialog"):
            yield Static("Allow this sensitive filesystem operation?")

            with VerticalScroll(id="sensitive-details"):
                yield Static(self.details, id="sensitive-info", markup=False) # <- Literal filenames
                
            with Horizontal(id="sensitive-buttons"):
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