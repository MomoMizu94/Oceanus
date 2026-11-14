""" Permission choices apply to the next submitted task """

from textual.widgets import Select

from agent_core.permissions import MODE_DESCRIPTIONS, MODE_LABELS, PermissionMode


class PermissionSelector(Select):
    def __init__(self, **kwargs):
        options = []
        for mode, label in MODE_LABELS.items():
            options.append((f"Permissions: {label}", mode.value))
        super().__init__(options, value=PermissionMode.ASK.value, allow_blank=False, **kwargs)
        self.task_busy = False
        self.locked_mode = PermissionMode.ASK
        self.tooltip = MODE_DESCRIPTIONS[PermissionMode.ASK]

    @property
    def mode(self) -> PermissionMode:
        if self.task_busy:
            return self.locked_mode
        return PermissionMode(self.value)

    def set_task_busy(self, busy: bool) -> None:
        if busy:
            self.locked_mode = self.mode
        self.task_busy = busy
        self.disabled = busy
        if not busy:
            self.value = self.locked_mode.value

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select is not self:
            return
        if self.task_busy:
            self.value = self.locked_mode.value
            return
        if event.value != self.value:
            return
        self.tooltip = MODE_DESCRIPTIONS[self.mode]
        if self.mode == PermissionMode.FULL:
            self.notify(self.tooltip, severity="warning", timeout=10)
