""" Choose the turn budget for the next task """
from textual.widgets import Select

from agent_core.loop import DEFAULT_MAX_TURNS


class TurnSelector(Select):
    def __init__(self, **kwargs):
        options = [(f"Turns: {count}", count) for count in (10, 20, 30, 50, 100)]
        super().__init__(options, value=DEFAULT_MAX_TURNS, allow_blank=False, **kwargs)
        self.task_busy = False
        self.locked_limit = DEFAULT_MAX_TURNS
        self.tooltip = "Maximum model turns per task, including recovery. The final turn cannot execute tools."

    @property
    def max_turns(self) -> int:
        if self.task_busy:
            return self.locked_limit
        return int(self.value)

    def set_task_busy(self, busy: bool) -> None:
        if busy:
            self.locked_limit = self.max_turns
        self.task_busy = busy
        self.disabled = busy
        if not busy:
            self.value = self.locked_limit

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select is self and self.task_busy:
            self.value = self.locked_limit
