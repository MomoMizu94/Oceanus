from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog, Select
from textual import work
from importlib.resources import files

from agent_core.loop import run_agent
from agent_core.approval_screen import ShellApprovalScreen


class OceanusApp(App):
    """ A terminal interface (TUI) for Oceanus """

    TITLE = "Oceanus"

    BINDINGS = [
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

    async def action_quit(self) -> None:
        if self.busy:
            self.notify("wait for the current task to finish before quitting.")
            return
        self.exit()

if __name__ == "__main__":
    OceanusApp().run()