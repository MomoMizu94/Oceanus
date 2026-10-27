from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from agent_core.web_approval import describe_web_search


class WebApprovalScreen(ModalScreen[bool]):
    """ Ask permission to send one search query to Tavily """

    BINDINGS = [
        ("y,Y", "approve", "Approve"),
        ("n,N", "deny", "Deny"),
        ("escape", "deny", "Deny"),
    ]

    # Keep the buttons visible while long queries scroll above them.
    DEFAULT_CSS = """
    WebApprovalScreen {
        align: center middle;
    }

    #web-dialog {
        width: 90%;
        max-width: 90;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #web-details {
        height: 1fr;
        margin: 1 0;
    }

    #web-buttons {
        height: auto;
        align-horizontal: right;
    }

    #web-buttons Button {
        margin-left: 1;
    }
    """
    def __init__(self, query: str) -> None:
        super().__init__()
        # Reuse the CLI disclosure and validate before opening the dialog
        self.description = describe_web_search(query)

    def compose(self) -> ComposeResult:
        with Vertical(id="web-dialog"):
            yield Static("Send this search query to Tavily?")
            with VerticalScroll(id="web-details"):
                # Brackets in a query must remain text
                yield Static(self.description, id="web-info", markup=False)
            with Horizontal(id="web-buttons"):
                yield Button("Deny", id="deny")
                yield Button("Approve", id="approve", variant="warning")

    def on_mount(self) -> None:
        # Enter denies unless the user deliberately moves to Approve
        self.query_one("#deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)