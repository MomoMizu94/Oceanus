from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from agent_core.fetch_approval import describe_page_fetch, describe_pdf_fetch, describe_page_render


class FetchApprovalScreen(ModalScreen[bool]):
    """Ask permission to fetch one page"""

    BINDINGS = [
        ("y,Y", "approve", "Approve"),
        ("n,N", "deny", "Deny"),
        ("escape", "deny", "Deny"),
    ]

    # Keep buttons visible while the URL and disclosure scroll
    DEFAULT_CSS = """
    FetchApprovalScreen {
        align: center middle;
    }

    #fetch-dialog {
        width: 90%;
        max-width: 90;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #fetch-details {
        height: 1fr;
        margin: 1 0;
    }

    #fetch-buttons {
        height: auto;
        align-horizontal: right;
    }

    #fetch-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, url: str, *, pdf: bool = False, rendered: bool = False) -> None:
        super().__init__()
        self.heading = "Fetch this PDF?" if pdf else "Fetch this page?"
        self.description = describe_pdf_fetch(url) if pdf else describe_page_fetch(url)

        if rendered:
            self.heading = "Render this page?"
            self.description = describe_page_render(url)

    def compose(self) -> ComposeResult:
        with Vertical(id="fetch-dialog"):
            yield Static(self.heading)
            with VerticalScroll(id="fetch-details"):
                # Brackets in URLs must remain literal text
                yield Static(self.description, id="fetch-info", markup=False)
            with Horizontal(id="fetch-buttons"):
                yield Button("Deny", id="deny")
                yield Button("Approve", id="approve", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
