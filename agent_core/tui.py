from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, Select
from textual.containers import VerticalScroll
from textual.binding import Binding
from textual import work
from importlib.resources import files
from pathlib import Path
from uuid import uuid4
from threading import Event
import asyncio
from http.client import HTTPException

from agent_core.loop import run_agent
from agent_core.approval_screen import ShellApprovalScreen
from agent_core.file_approval_screen import FileApprovalScreen
from agent_core.memory.session import load_session, save_session
from agent_core.session_picker import SessionPicker
from agent_core.model_client import DEFAULT_MODEL
from agent_core.model_discovery import list_ollama_models
from agent_core.edit_approval_screen import EditApprovalScreen
from agent_core.sensitive_approval_screen import SensitiveApprovalScreen
from agent_core.instructions import build_system_prompt
from agent_core.web_approval_screen import WebApprovalScreen
from agent_core.fetch_approval_screen import FetchApprovalScreen
from agent_core.conversation_message import ConversationMessage
from agent_core.wal_theme import load_wal_theme


class OceanusApp(App):
    """ A terminal interface (TUI) for Oceanus """

    TITLE = "Oceanus"

    BINDINGS = [
        Binding("ctrl+x", "cancel_task", "Cancel task", priority=True),
        ("ctrl+o", "open_session", "Open session"),
        ("ctrl+s", "save_session", "Save session"),
        ("ctrl+q", "quit", "Quit")
    ]

    # Style
    CSS = """
    Screen {
        layout: vertical;
    }

    #conversation {
        height: 1fr;
        padding: 1 2;
        align-horizontal: center;
    }

    #conversation > ConversationMessage {
        width: 1fr;
        max-width: 80;
    }

    #task-input {
        dock: bottom;
        margin: 1 0;
    }
    """

    def __init__(self) -> None:
        """ App state initialization """
        super().__init__()

        # Load wallpaper palette before building the interface
        self.wal_path = Path.home() / ".cache" / "wal" / "colors.json"
        wal_theme = load_wal_theme(self.wal_path)
        self.wal_theme = wal_theme

        if wal_theme is not None:
            self.register_theme(wal_theme)
            self.theme = wal_theme.name

        self.model = DEFAULT_MODEL
        self.busy = False
        self.cancel_event = Event()
        self.conversation_history: list[dict] = []
        self.session_path = (
            Path.home() / ".local" / "share" / "oceanus" / "sessions"
            / f"session-{uuid4().hex}.json"
        )
        self.coding_prompt = (
            files("agents.coding")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )
        

    def compose(self) -> ComposeResult:
        """ Supplied widgets on screen """
        yield Header()
        # Drop-down model selection
        yield Select(
            [
                ("GPT-4.1 - OpenAI API", DEFAULT_MODEL)
            ],
            value=self.model,
            allow_blank=False,
            disabled=True,
            id="model-select"
        )

        yield VerticalScroll(id="conversation")
        yield Input(
            placeholder="Type your message and press Enter",
            disabled=True,
            id="task-input"
        )
        yield Footer()

    async def on_mount(self) -> None:
        # Runs after interface mounts
        self.set_interval(1.0, self.reload_wal_theme)
        self.sub_title = "Loading Ollama models..."
        selector = self.query_one("#model-select", Select)
        task_input = self.query_one("#task-input", Input).focus()

        try:
            names = await asyncio.to_thread(list_ollama_models)
        except (OSError, ValueError, HTTPException):
            names = []
            self.notify("Could not list Ollama models. Only GPT-4.1 is listed.", severity="warning")

        local_options = [
            (f"{name} - Ollama", f"ollama_chat/{name}")
            for name in names
        ]
        selector.set_options(
            [
                ("GPT-4.1 - OpenAI API", DEFAULT_MODEL),
                *local_options,
            ]
        )

        preferred = "ollama_chat/qwen2.5:7b"
        available = {value for _, value in local_options}

        if preferred in available:
            self.model = preferred
        else:
            self.model = local_options[0][1] if local_options else DEFAULT_MODEL

        selector.value = self.model
        selector.disabled = False
        task_input.disabled = False
        self.sub_title = self.model
        task_input.focus()

    def reload_wal_theme(self) -> None:
        new_theme = load_wal_theme(self.wal_path)

        # Keep the current colors until a complete palette is available
        if new_theme is None or new_theme ==  self.wal_theme:
            return

        self.wal_theme = new_theme
        self.register_theme(new_theme)

        if self.theme == new_theme.name:
            # The name stayed the same. Colors changed
            # Only assigning the name would not trigger Textual theme update
            self.mutate_reactive(App.theme)
        else:
            self.theme = new_theme.name

    def write_message(
        self,
        message: str,
        *,
        kind: str = "notice",
        heading: str | None = None,
    ) -> None:
        conversation = self.query_one("#conversation", VerticalScroll)
        follow_output = conversation.is_vertical_scroll_end
        entry = ConversationMessage(kind, message, heading=heading)
        conversation.mount(entry)

        # Follow later layout changes while at the bottom
        if follow_output:
            conversation.anchor()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Runs on Enter key pressed
        task = event.value.strip()

        if not task or self.busy:
            return

        self.cancel_event.clear()
        self.busy = True
        # Disables model selection if busy
        self.query_one("#model-select", Select).disabled = True
        self.sub_title = f"{self.model} | Working..."
        self.write_message(task, kind="user")
        
        event.input.clear()
        event.input.disabled = True

        self.run_task(task, self.conversation_history.copy(), self.model)

    def report_progress(self, message: str) -> None:
        # The loop adds this prefix to intermediate agent replies
        if message.startswith("Agent: "):
            text = message.removeprefix("Agent: ")
            self.call_from_thread(self.write_message, text, kind="assistant")
        else:
            self.call_from_thread(self.write_message, message, kind="status")

    def report_tool_result(self, tool_name: str, result: str) -> None:
        self.call_from_thread(
            self.write_message,
            result,
            kind="tool",
            heading=f"Tool result: {tool_name}",
        )

    def report_tool_status(self, tool_name: str, status: str) -> None:
        self.call_from_thread(
            self.write_message,
            status,
            kind="status",
            heading=f"Tool execution: {tool_name}",
        )

    def request_file_approval(self, operation: str, path: str) -> bool:
        # Schedules show_file_approval to the UI thread
        return self.call_from_thread(self.show_file_approval, operation, path)    

    def request_approval(self, command: str, working_directory: str) -> bool:
        # Schedules show_shell_approval to the UI thread
        return self.call_from_thread(self.show_shell_approval, command, working_directory)

    def request_search_approval(self, path: str, query: str, pattern: str) -> bool:
        return self.call_from_thread(self.show_search_approval, path, query, pattern)

    def request_edit_approval(self, path: str, diff: str) -> bool:
        # Schedules show_shell_approval to the UI thread
        return self.call_from_thread(self.show_edit_approval, path, diff)

    def request_sensitive_approval(self, operation: str, path: str, reason: str) -> bool:
        return self.call_from_thread(self.show_sensitive_approval, operation, path, reason)

    def request_discovery_approval(self, path: str, pattern: str) -> bool:
        return self.call_from_thread(self.show_discovery_approval, path, pattern)

    def request_web_approval(self, query: str) -> bool:
        return self.call_from_thread(self.show_web_approval, query)

    def request_fetch_approval(self, url: str) -> bool:
        return self.call_from_thread(self.show_fetch_approval, url)

    def on_select_changed(self, event: Select.Changed) -> None:
        """ Called when model selection changes. Updates model & header.
         Busy check rejects selection that was triggered after task start. """
        if event.select.id != "model-select":
            return

        if self.busy:
            event.select.value = self.model
            return

        if not isinstance(event.value, str) or event.value != event.select.value:
            return

        self.model = event.value
        self.sub_title = self.model

    async def show_file_approval(self, operation: str, path: str) -> bool:
        # Avoid opening a dialog after cancellation
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(FileApprovalScreen(operation, path))
        return decision is True and not self.cancel_event.is_set()

    async def show_shell_approval(self, command: str, working_directory: str) -> bool:
        # Avoid openining a dialog after cancellation
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(ShellApprovalScreen(command, working_directory))
        return decision is True and not self.cancel_event.is_set()

    async def show_search_approval(self, path: str, query: str, pattern: str) -> bool:
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(FileApprovalScreen("search", path, query=query, pattern=pattern))
        return decision is True and not self.cancel_event.is_set()

    async def show_edit_approval(self, path: str, diff: str) -> bool:
        # Avoid openining a dialog after cancellation
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(EditApprovalScreen(path, diff))
        return decision is True and not self.cancel_event.is_set()

    async def show_sensitive_approval(self, operation: str, path: str, reason: str) -> bool:
        # Avoid openining a dialog after cancellation
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(SensitiveApprovalScreen(operation, path, reason))
        return decision is True and not self.cancel_event.is_set()

    async def show_discovery_approval(self, path: str, pattern: str) -> bool:
        if self.cancel_event.is_set():
            return False

        screen = FileApprovalScreen("discovery", path, pattern=pattern)
        decision = await self.push_screen_wait(screen)

        if decision is not True:
            return False

        if self.cancel_event.is_set():
            return False

        return True

    async def show_web_approval(self, query: str) -> bool:
        # Cancellation can arrive before UI receives the request
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(WebApprovalScreen(query))
        if decision is not True:
            return False

        # Cancellation can arrive while user is deciding
        if self.cancel_event.is_set():
            return False

        return True
    

    async def show_fetch_approval(self, url: str) -> bool:
        # Cancellation may arrive before or during the dialog
        if self.cancel_event.is_set():
            return False

        decision = await self.push_screen_wait(FetchApprovalScreen(url))
        return decision is True and not self.cancel_event.is_set()

    @work(thread=True)
    def run_task(self, task: str, history: list[dict], model: str) -> None:
        """ Runs the prompt. Executed in a background thread. """
        try:
            coding_prompt = build_system_prompt(self.coding_prompt)
            answer = run_agent(
                task,
                model=model,
                system_prompt=coding_prompt,
                conversation_history=history,
                on_progress=self.report_progress,
                on_approval=self.request_approval,
                on_file_approval=self.request_file_approval,
                on_tool_result=self.report_tool_result,
                should_cancel=self.cancel_event.is_set,
                on_tool_status=self.report_tool_status,
                on_search_approval=self.request_search_approval,
                on_edit_approval=self.request_edit_approval,
                on_sensitive_approval=self.request_sensitive_approval,
                on_discovery_approval=self.request_discovery_approval,
                on_web_approval=self.request_web_approval,
                on_fetch_approval=self.request_fetch_approval,
            )
        except Exception as error:
            self.call_from_thread(
                self.finish_task,
                f"{type(error).__name__}: {error}",
                None,
            )
        else:
            self.call_from_thread(self.finish_task, answer, history)

    def finish_task(self, message: str, history: list[dict] | None) -> None:
        if history is None:
            kind = "error"
        else:
            self.conversation_history = history
            kind = "assistant"

        self.write_message(message, kind=kind)
        self.busy = False
        self.sub_title = self.model
        self.query_one("#model-select", Select).disabled = False

        task_input = self.query_one("#task-input", Input)
        task_input.disabled = False
        task_input.focus()

    def action_open_session(self) -> None:
        # Do not open another picker over an existing dialog
        if len(self.screen_stack) > 1:
            return

        if self.busy:
            self.notify("Wait for the current task to finish before opening a session.")
            return

        task_input = self.query_one("#task-input", Input)

        if self.conversation_history or task_input.value:
            self.notify(
                "Open sessions in a fresh TUI."
                "Save you conversation and restart first."
            )
            return

        try:
            paths = [
                path
                for path in self.session_path.parent.glob("*json")
                if path.is_file()
            ]
            paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError as error:
            self.notify(f"Could not list sessions: {error}", severity="error")
            return

        if not paths:
            self.notify("No saved sessions found.")
            return

        self.push_screen(SessionPicker(paths), self.restore_session)

    def restore_session(self, path: Path | None) -> None:
        if path is None:
            return

        try:
            history = load_session(path)
            if not history:
                raise ValueError("This session is empty!")
        except (OSError, ValueError) as error:
            self.notify(f"Could not open session: {error}", severity="error")
            return

        self.conversation_history = history
        self.session_path = path
        conversation = self.query_one("#conversation", VerticalScroll)
        conversation.remove_children()

        # Display saved content without changing the model's history
        for message in history:
            role = message.get("role")
            content = message.get("content")
            if role in ("user", "assistant", "tool") and isinstance(content, str):
                self.write_message(content, kind=role)

        self.write_message(f"Session opened: {path}")
        conversation.scroll_end(animate=False)
        self.query_one("#task-input", Input).focus()

    def action_save_session(self) -> None:
        """ Checks if saving is appropriate, creates directory and calls for helper """
        if self.busy:
            self.notify("Wait for the current task to finish before saving.")
            return

        if not self.conversation_history:
            self.notify("There is no conversation to save yet.")
            return

        try:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            save_session(self.session_path, self.conversation_history)
        except (OSError, ValueError, TypeError) as error:
            self.notify(f"Could not save the session: {error}", severity="error")
            return

        self.write_message(f"Session saved: {self.session_path}")

    def action_cancel_task(self) -> None:
        if not self.busy or self.cancel_event.is_set():
            return

        self.cancel_event.set()
        self.sub_title = f"{self.model} | Cancelling..."

        if isinstance(self.screen, (ShellApprovalScreen, FileApprovalScreen, EditApprovalScreen, SensitiveApprovalScreen, WebApprovalScreen, FetchApprovalScreen)):
            self.screen.dismiss(False)

        self.notify("Cancellation requested. Waiting for the current operation to finish.")

    async def action_quit(self) -> None:
        if self.busy:
            self.notify("wait for the current task to finish before quitting.")
            return
        self.exit()

if __name__ == "__main__":
    OceanusApp().run()
