from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from agent_core.shell_sandbox import validate_workspace_root


class WorkspaceScreen(ModalScreen[Path | None]):
    """Choose a workspace for an older session that has no saved folder"""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    WorkspaceScreen { align: center middle; }
    #workspace-dialog {
        width: 90%; max-width: 100; height: auto;
        padding: 1 2; border: round $primary; background: $surface;
    }
    #workspace-input { margin: 1 0; }
    #workspace-error { height: auto; color: $error; }
    #workspace-buttons { height: auto; align-horizontal: right; }
    #workspace-buttons Button { margin-left: 1; }
    """

    def __init__(self, current: Path) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="workspace-dialog"):
            yield Static("This session has no saved project folder. Choose where to resume it.")
            yield Input(str(self.current), id="workspace-input")
            yield Static("", id="workspace-error", markup=False)
            with Horizontal(id="workspace-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Open session", id="open", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#workspace-input", Input).focus()

    def open_workspace(self) -> None:
        value = self.query_one("#workspace-input", Input).value
        try:
            if not value.strip():
                raise ValueError("Choose a project folder")
            workspace = validate_workspace_root(Path(value))
        except (OSError, ValueError) as error:
            self.query_one("#workspace-error", Static).update(str(error))
            return
        self.dismiss(workspace)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.open_workspace()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "open":
            self.open_workspace()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
