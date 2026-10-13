from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from agent_core.edit_approval import describe_edit


class EditApprovalScreen(ModalScreen[bool]):
    """ Review the complete diff for one edit """

    BINDINGS = [
        ("y, Y", "approve", "Approve"),
        ("n, N", "deny", "Deny"),
        ("escape", "deny", "Deny")
    ]

    # Style
    DEFAULT_CSS = """
    EditApprovalScreen {
        align: center middle;
    }

    #edit-dialog {
        width: 90%;
        max-width: 90;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #edit-details {
        height: 1fr;
        margin: 1 0;
    }

    #edit-buttons {
        height: auto;
        align-horizontal: right;
    }

    #edit-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, path:str , diff: str) -> None:
        """ Approval screen initialization """
        super().__init__()
        self.path = path
        self.diff = diff

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-dialog"):
            yield Static("Allow this file edit?")

            with VerticalScroll(id="edit-details"):
                yield Static(describe_edit(self.path, self.diff), id="edit-info", markup=False)
                
            with Horizontal(id="edit-buttons"):
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