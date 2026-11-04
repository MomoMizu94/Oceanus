"""Model-specific reasoning choices for the terminal interface"""

import asyncio
from http.client import HTTPException

from textual import work
from textual.widgets import Select

from agent_core.model_client import default_reasoning
from agent_core.model_discovery import ollama_reasoning_modes


class ReasoningSelector(Select):
    def __init__(self, **kwargs):
        super().__init__([("Reasoning: model default", "auto")],
                         value="auto", allow_blank=False, disabled=True, **kwargs)
        self.model = ""
        self.loading_modes = False
        self.task_busy = False
        self.locked_value = "auto"
        self.revision = 0
        self.modes = {"auto": None}

    @property
    def reasoning(self) -> str | bool | None:
        return self.modes.get(self.value)

    def set_task_busy(self, busy: bool) -> None:
        if busy:
            self.locked_value = self.value
        self.task_busy = busy
        self.disabled = busy or self.loading_modes or len(self.modes) < 2

    def set_model(self, model: str) -> None:
        if model == self.model:
            return
        self.model = model
        self.revision += 1
        self.modes = {"auto": None}
        preferred = default_reasoning(model)
        label = "model default" if preferred is None else f"{preferred} (default)"
        self.set_options([(f"Reasoning: {label}", "auto")])
        self.value = "auto"
        self.disabled = True
        self.tooltip = "Reasoning choices are available for local Ollama models"
        self.loading_modes = model.startswith("ollama_chat/")
        if self.loading_modes:
            self.tooltip = "Loading this model's reasoning modes"
            self.load_modes(model, self.revision, label)

    @work(exclusive=True)
    async def load_modes(self, model: str, revision: int, default_label: str) -> None:
        try:
            values = await asyncio.to_thread(ollama_reasoning_modes, model.split("/", 1)[1])
        except (OSError, ValueError, HTTPException):
            values = []
            tooltip = "Could not read reasoning modes; using the default setting"
        else:
            tooltip = "Applies to the next task; selection resets when switching models"
            if not values:
                tooltip = "This model does not report selectable reasoning modes"

        # A slow lookup must not replace choices for a newer model selection
        if model != self.model or revision != self.revision:
            return
        options = [(f"Reasoning: {default_label}", "auto")]
        modes = {"auto": None}
        for index, value in enumerate(values):
            key = f"mode-{index}"
            if value is False:
                label = "off"
            elif value is True:
                label = "on"
            else:
                label = value
            options.append((f"Reasoning: {label}", key))
            modes[key] = value
        self.modes = modes
        self.set_options(options)
        self.value = "auto"
        self.tooltip = tooltip
        self.loading_modes = False
        self.disabled = self.task_busy or len(modes) < 2

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select is self and self.task_busy:
            self.value = self.locked_value
