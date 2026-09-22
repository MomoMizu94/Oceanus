from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog
from textual import work
from importlib.resources import files

from agent_core.loop import run_agent


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
        self.sub_title = f"{self.model} | Working..."
        self.write_message(f"You: {task}")
        event.input.clear()
        event.input.disabled = True
        self.run_task(task, self.conversation_history.copy())

    def report_progress(self, message: str) -> None:
        """ Progress callback function """
        self.call_from_thread(self.write_message, message)

    @work(thread=True)
    def run_task(self, task: str, history: list[dict]) -> None:
        """ Runs the prompt. Executed in a background thread. """
        try:
            answer = run_agent(
                task,
                model=self.model,
                system_prompt=self.coding_prompt,
                conversation_history=history,
                on_progress=self.report_progress,
                on_approval=None
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