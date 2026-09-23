from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog, Select
from textual import work
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from agent_core.loop import run_agent
from agent_core.approval_screen import ShellApprovalScreen
from agent_core.memory.session import load_session, save_session
from agent_core.session_picker import SessionPicker


class OceanusApp(App):
    """ A terminal interface (TUI) for Oceanus """

    TITLE = "Oceanus"

    BINDINGS = [
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
        border: round $primary;
        padding: 0 1;
    }

    #task-input {
        dock: bottom;
        margin: 1 0;
    }
    """

    def __init__(self) -> None:
        """ App state initialization """
        super().__init__()
        self.model = "ollama_chat/qwen2.5:7b"
        self.busy = False
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
                ("Qwen 2.5 - local", "ollama_chat/qwen2.5:7b"),
                ("GPT-4.1 mini - OpenAI API", "openai/gpt-4.1-mini"),
            ],
            value=self.model,
            allow_blank=False,
            id="model-select"
        )

        yield RichLog(
            id="conversation",
            wrap=True,
            min_width=1,
            markup=False,
            highlight=False
        )
        yield Input(
            placeholder="Type your message and press Enter",
            id="task-input"
        )
        yield Footer()

    def on_mount(self) -> None:
        # Runs after interface mounts
        self.sub_title = self.model
        self.query_one("#task-input", Input).focus()

    def write_message(self, message: str) -> None:
        self.query_one("#conversation", RichLog).write(message)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Runs on Enter key pressed
        task = event.value.strip()

        if not task or self.busy:
            return

        self.busy = True
        # Disables model selection if busy
        self.query_one("#model-select", Select).disabled = True
        self.sub_title = f"{self.model} | Working..."
        self.write_message(f"You: {task}")
        
        event.input.clear()
        event.input.disabled = True

        self.run_task(task, self.conversation_history.copy(), self.model)

    def report_progress(self, message: str) -> None:
        """ Progress callback function """
        self.call_from_thread(self.write_message, message)

    def report_tool_result(self, tool_name: str, result: str) -> None:
        self.call_from_thread(self.write_message, f"Tool result: ({tool_name}):\n{result}")

    def request_approval(self, command: str, working_directory: str) -> bool:
        # Schedules show_shell_approval to the UI thread
        return self.call_from_thread(self.show_shell_approval, command, working_directory)

    def on_select_changed(self, event: Select.Changed) -> None:
        """ Called when model selection changes. Updates model & header.
         Busy check rejects selection that was triggered after task start. """
        if event.select.id != "model-select":
            return

        if self.busy:
            event.select.value = self.model
            return

        if not isinstance(event.value, str):
            return

        self.model = event.value
        self.sub_title = self.model

    async def show_shell_approval(self, command: str, working_directory: str) -> bool:
        decision = await self.push_screen_wait(ShellApprovalScreen(command, working_directory))
        return decision is True
    

    @work(thread=True)
    def run_task(self, task: str, history: list[dict], model: str) -> None:
        """ Runs the prompt. Executed in a background thread. """
        try:
            answer = run_agent(
                task,
                model=model,
                system_prompt=self.coding_prompt,
                conversation_history=history,
                on_progress=self.report_progress,
                on_approval=self.request_approval,
                on_tool_result=self.report_tool_result
            )
        except Exception as error:
            self.call_from_thread(self.finish_task, f"Error: {type(error).__name__}: {error}", None)
        else:
            self.call_from_thread(
                self.finish_task,
                f"Agent: {answer}",
                history
            )

    def finish_task(self, message: str, history: list[dict] | None) -> None:
        if history is not None:
            self.conversation_history = history

        self.write_message(message)
        self.busy = False
        self.sub_title = self.model
        # Re-enable model selection
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

        self.query_one("#conversation", RichLog).clear()

        labels = {
            "user": "You",
            "assistant": "Agent",
            "tool": "Tool result"
        }

        for message in history:
            role = message.get("role")
            content = message.get("content")

            if role in ("user", "assistant", "tool") and isinstance(content, str):
                self.write_message(f"{labels[role]}: {content}")

        self.write_message(f"Session opened: {path}")
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

    async def action_quit(self) -> None:
        if self.busy:
            self.notify("wait for the current task to finish before quitting.")
            return
        self.exit()

if __name__ == "__main__":
    OceanusApp().run()