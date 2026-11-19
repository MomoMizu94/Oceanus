import asyncio
from datetime import datetime
from pathlib import Path
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from agent_core.memory.session import load_session_state


def workspace_sessions(directory: Path, workspace: Path) -> list[tuple[float, Path, str]]:
    rows = []
    root = workspace.resolve()
    for path in directory.glob("*.json"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            history, _, folder = load_session_state(path)
            if not history or folder is None or folder.resolve() != root:
                continue
            title = path.stem
            for message in history:
                if message.get("role") == "user" and isinstance(message.get("content"), str):
                    title = " ".join(message["content"].split())[:90] or "Image attachment"
                    break
            rows.append((path.stat().st_mtime, path, title))
        except (ValueError, OSError, RuntimeError):
            # A damaged or concurrently removed checkpoint should not hide the others
            continue
    return sorted(rows, reverse=True)


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

    def __init__(self, paths: list[Path], *, directory: Path | None = None, workspace: Path | None = None) -> None:
        """ Session screen initialization """
        super().__init__()
        self.paths = paths
        self.directory = directory
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        with Vertical(id="session-dialog"):
            yield Static("Continue in this workspace" if self.workspace else "Open session, newest first")
            yield OptionList(
                *(Text(path.name) for path in self.paths),
                id="session-list"
            )
            yield Static("↑↓ select   Enter open   Esc close", id="session-hint", markup=False)

    def on_mount(self) -> None:
        options = self.query_one("#session-list", OptionList)
        options.focus()
        if self.workspace is not None and self.directory is not None:
            hint = self.query_one("#session-hint", Static)
            hint.update("Reading workspace conversations…")
            self.load_workspace_sessions(options, hint)
        elif self.paths:
            options.highlighted = 0

    @work
    async def load_workspace_sessions(self, options: OptionList, hint: Static) -> None:
        try:
            rows = await asyncio.to_thread(workspace_sessions, self.directory, self.workspace)
        except OSError as error:
            hint.update(f"Could not list sessions: {error}")
            return
        self.paths = [path for _, path, _ in rows]
        for modified, _, title in rows:
            date = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
            options.add_option(Text(f"{date}   {title}"))
        if rows:
            options.highlighted = 0
            hint.update("↑↓ select   Enter resume   Esc close")
        else:
            hint.update("No saved conversations in this workspace. Esc closes this list.")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.paths[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)
