from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Select, Static
from textual.containers import Horizontal, VerticalScroll
from textual.binding import Binding
from textual import work
from importlib.resources import files
from pathlib import Path
import os
from threading import Event
from copy import deepcopy
import asyncio
from http.client import HTTPException

from agent_core.loop import run_agent
from agent_core.approval_screen import ShellApprovalScreen
from agent_core.file_approval_screen import FileApprovalScreen
from agent_core.memory.session import save_session
from agent_core.memory.recovery import (
    RECOVERY_NOTICE, has_interrupted_tools, load_recovery_session,
    new_session_path, prepare_recovery_messages,
)
from agent_core.shell_sandbox import validate_workspace_root
from agent_core.workspace_screen import WorkspaceScreen
from agent_core.session_picker import SessionPicker
from agent_core.model_client import DEFAULT_MODEL
from agent_core.model_discovery import list_ollama_models
from agent_core.edit_approval_screen import EditApprovalScreen
from agent_core.sensitive_approval_screen import SensitiveApprovalScreen
from agent_core.instructions import build_system_prompt
from agent_core.skills import prepare_skill_prompt
from agent_core.tools.registry import TOOL_REGISTRY
from agent_core.web_approval_screen import WebApprovalScreen
from agent_core.fetch_approval_screen import FetchApprovalScreen
from agent_core.conversation_message import ConversationMessage
from agent_core.wal_theme import load_wal_theme
from agent_core.prompt_editor import PromptEditor
from agent_core.reasoning_selector import ReasoningSelector
from agent_core.permission_selector import PermissionSelector
from agent_core.permissions import PermissionMode, permission_instructions


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

    #workspace-controls {
        height: 1;
    }

    #permission-select {
        width: 34;
    }

    #workspace-path {
        width: 1fr;
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    #model-controls {
        height: auto;
    }

    #model-select {
        width: 2fr;
    }

    #reasoning-select {
        width: 1fr;
        min-width: 28;
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
        self.active_messages: list[dict] | None = None
        self.workspace = Path.cwd().resolve()
        self.session_path = new_session_path()
        self.coding_prompt = (
            files("agents.coding")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )
        

    def compose(self) -> ComposeResult:
        """ Supplied widgets on screen """
        yield Header()
        with Horizontal(id="workspace-controls"):
            workspace_label = Static(str(self.workspace), id="workspace-path", markup=False)
            workspace_label.tooltip = str(self.workspace)
            yield workspace_label
            yield PermissionSelector(id="permission-select", compact=True)
        # Drop-down model selection
        with Horizontal(id="model-controls"):
            yield Select(
                [("GPT-4.1 - OpenAI API", DEFAULT_MODEL)],
                value=self.model,
                allow_blank=False,
                disabled=True,
                id="model-select"
            )
            yield ReasoningSelector(id="reasoning-select")

        yield VerticalScroll(id="conversation")
        yield PromptEditor(
            disabled=True,
            id="task-input"
        )
        yield Footer()

    async def on_mount(self) -> None:
        # Runs after interface mounts
        self.set_interval(1.0, self.reload_wal_theme)
        self.sub_title = "Loading Ollama models..."
        selector = self.query_one("#model-select", Select)
        task_input = self.query_one("#task-input", PromptEditor).focus()

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
        self.query_one(ReasoningSelector).set_model(self.model)
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

    def write_user_message(self, text: str, attachments=None) -> None:
        heading = None
        if isinstance(attachments, list) and attachments:
            count = len(attachments)
            noun = "image" if count == 1 else "images"
            heading = f"You · {count} {noun}"
        self.write_message(text, kind="user", heading=heading)

    def on_prompt_editor_submitted(self, event: PromptEditor.Submitted) -> None:
        event.stop()
        task_input = self.query_one("#task-input", PromptEditor)
        task = event.text

        if self.busy or task_input.disabled or task_input.read_only or task_input.paste_pending:
            return
        # Keep image-only prompts and preserve the user's exact text
        if not task.strip() and not event.attachments:
            return

        attachments = deepcopy(event.attachments)
        self.cancel_event.clear()
        self.busy = True
        self.query_one("#model-select", Select).disabled = True
        reasoning_selector = self.query_one(ReasoningSelector)
        reasoning = reasoning_selector.reasoning
        reasoning_selector.set_task_busy(True)
        permission_selector = self.query_one(PermissionSelector)
        permission_mode = permission_selector.mode
        permission_selector.set_task_busy(True)
        self.sub_title = f"{self.model} | Working..."
        if not self.conversation_history:
            self.write_message(f"Recovery checkpoints: {self.session_path}", kind="status")
        self.write_user_message(task, attachments)

        # Start a fresh draft with no undo history from the sent prompt
        task_input.load_text("")
        task_input.disabled = True

        self.run_task(
            task, deepcopy(self.conversation_history), self.model,
            attachments=attachments,
            reasoning=reasoning,
            permission_mode=permission_mode,
            active_messages=deepcopy(self.active_messages),
        )

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
        self.query_one(ReasoningSelector).set_model(self.model)

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
    def run_task(
        self, task: str, history: list[dict], model: str,
        *, attachments: list[dict] | None = None,
        reasoning: str | bool | None = None,
        permission_mode: PermissionMode = PermissionMode.ASK,
        active_messages: list[dict] | None = None,
    ) -> None:
        """ Runs the prompt. Executed in a background thread. """
        if active_messages is None:
            active_messages = deepcopy(history)
        try:
            coding_prompt = build_system_prompt(self.coding_prompt, workspace=self.workspace)
            coding_prompt, skill_notices = prepare_skill_prompt(
                coding_prompt, task, available_tools=TOOL_REGISTRY, workspace=self.workspace,
            )
            coding_prompt += permission_instructions(permission_mode)
            for notice in skill_notices:
                self.report_progress(notice)
            answer = run_agent(
                task,
                model=model,
                system_prompt=coding_prompt,
                conversation_history=history,
                active_messages=active_messages,
                checkpoint_path=self.session_path,
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
                attachments=attachments,
                reasoning=reasoning,
                permission_mode=permission_mode,
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            try:
                # A failed tool may have acted before it raised an unexpected error
                recovered_history = prepare_recovery_messages(history)
                recovered_active = prepare_recovery_messages(active_messages)
                history = recovered_history
                active_messages = recovered_active
            except ValueError as recovery_error:
                message += f" History could not be prepared for recovery: {recovery_error}"
            self.call_from_thread(
                self.finish_task,
                message,
                history,
                failed=True,
                active_messages=active_messages,
            )
        else:
            self.call_from_thread(
                self.finish_task, answer, history, active_messages=active_messages,
            )

    def finish_task(
        self, message: str, history: list[dict] | None, *, failed: bool = False,
        active_messages: list[dict] | None = None,
    ) -> None:
        # Keep submitted attachments and completed tool actions after a failure
        if history is not None:
            self.conversation_history = history
            self.active_messages = active_messages
        if failed or history is None:
            kind = "error"
        else:
            kind = "assistant"

        self.write_message(message, kind=kind)
        self.busy = False
        self.sub_title = self.model
        self.query_one("#model-select", Select).disabled = False
        self.query_one(ReasoningSelector).set_task_busy(False)
        self.query_one(PermissionSelector).set_task_busy(False)

        task_input = self.query_one("#task-input", PromptEditor)
        task_input.disabled = False
        task_input.focus()

    def action_open_session(self) -> None:
        # Do not open another picker over an existing dialog
        if len(self.screen_stack) > 1:
            return

        if self.busy:
            self.notify("Wait for the current task to finish before opening a session.")
            return

        task_input = self.query_one("#task-input", PromptEditor)

        if task_input.paste_pending:
            self.notify("Wait for the image paste to finish before opening a session.")
            return

        if self.conversation_history or task_input.text:
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

        if self.busy or self.query_one("#task-input", PromptEditor).paste_pending:
            self.notify("Wait for the current operation before opening a session.")
            return

        try:
            path = path.resolve()
            history, active_messages, workspace = load_recovery_session(path)
            if not history:
                raise ValueError("This session is empty!")
        except (OSError, ValueError) as error:
            self.notify(f"Could not open session: {error}", severity="error")
            return

        if workspace is None:
            def choose_workspace(selected: Path | None) -> None:
                if selected is not None:
                    self.apply_session(path, history, selected, active_messages=active_messages)

            self.push_screen(WorkspaceScreen(self.workspace), choose_workspace)
            return

        self.apply_session(path, history, workspace, active_messages=active_messages)

    def apply_session(
        self, path: Path, history: list[dict], workspace: Path,
        *, active_messages: list[dict] | None = None,
    ) -> None:
        if self.busy or self.query_one("#task-input", PromptEditor).paste_pending:
            self.notify("Wait for the current operation before opening a session.")
            return
        try:
            workspace = validate_workspace_root(workspace)
            # Tools use the process cwd; switch only while the agent is idle
            os.chdir(workspace)
        except (OSError, ValueError) as error:
            self.notify(f"Could not open the session's project folder: {error}", severity="error")
            return

        self.workspace = workspace
        self.query_one(PermissionSelector).value = PermissionMode.ASK.value
        workspace_label = self.query_one("#workspace-path", Static)
        workspace_label.update(str(workspace))
        workspace_label.tooltip = str(workspace)
        self.conversation_history = history
        if active_messages is None:
            active_messages = deepcopy(history)
        self.active_messages = active_messages
        self.session_path = path
        conversation = self.query_one("#conversation", VerticalScroll)
        conversation.remove_children()

        # Display saved content without changing the model's history
        for message in history:
            role = message.get("role")
            content = message.get("content")
            if isinstance(content, str):
                if role == "user":
                    self.write_user_message(content, message.get("attachments"))
                elif role in ("assistant", "tool"):
                    self.write_message(content, kind=role)

        self.write_message(f"Session opened: {path}")
        if has_interrupted_tools(history) or has_interrupted_tools(active_messages):
            self.write_message(RECOVERY_NOTICE, kind="notice")
        conversation.scroll_end(animate=False)
        self.query_one("#task-input", PromptEditor).focus()

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
            save_session(
                self.session_path, self.conversation_history, workspace=self.workspace,
                active_messages=self.active_messages,
            )
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
