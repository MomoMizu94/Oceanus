from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ShellApprovalScreen(ModalScreen[bool]):
    """ Ask whether one shell command may run """

    BINDINGS = [
        ("y, Y", "approve", "Approve"),
        ("n, N", "deny", "Deny"),
        ("escape", "deny", "Deny")
    ]

    # Style
    DEFAULT_CSS = """
    ShellApprovalScreen {
        align: center middle;
    }

    #shell-dialog {
        width: 90%;
        max-width: 90;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #shell-details {
        height: 1fr;
        margin: 1 0;
    }

    #shell-buttons {
        height: auto;
        align-horizontal: right;
    }

    #shell-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, command: str, working_directory: str) -> None:
        """ Approval screen initialization """
        super().__init__()
        self.command = command
        self.working_directory = working_directory

    def compose(self) -> ComposeResult:
        with Vertical(id="shell-dialog"):
            yield Static("Allow this shell command?")
            with VerticalScroll(id="shell-details"):
                yield Static(
                    f"Working directory:\n{self.working_directory}\n\n"
                    f"Command:\n{self.command}",
                    markup=False
                )
            with Horizontal(id="shell-buttons"):
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