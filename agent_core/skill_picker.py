from collections import Counter
import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from agent_core.skills import Skill, discover_skills


def skill_options(catalog: list[Skill], warnings: list[str], prefix: str = "") -> list[Option]:
    counts = Counter(skill.name for skill in catalog)
    entries = []
    for skill in catalog:
        if not skill.name.startswith(prefix):
            continue
        if counts[skill.name] > 1:
            label = Text(f"${skill.name} (duplicate name)\n{skill.path}")
            entries.append(Option(label, disabled=True))
        else:
            label = Text(f"${skill.name}\n{skill.description}")
            entries.append(Option(label, id=skill.name))
    for warning in warnings:
        entries.append(Option(Text(warning), disabled=True))
    return entries


class SkillPicker(ModalScreen[str | None]):
    """Choose a skill name to insert into the current prompt."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    SkillPicker { align: center middle; }
    #skill-dialog {
        width: 90%; max-width: 90; height: 70%;
        padding: 1 2; border: round $primary; background: $surface;
    }
    #skill-options { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-dialog"):
            yield Static("Choose a skill")
            yield OptionList(id="skill-options")
            yield Static("Loading skills…", id="skill-hint", markup=False)

    async def on_mount(self) -> None:
        options = self.query_one(OptionList)
        hint = self.query_one("#skill-hint", Static)
        try:
            catalog, warnings = await asyncio.to_thread(discover_skills)
        except (OSError, ValueError) as error:
            hint.update(f"Could not load skills: {error}")
            return

        entries = skill_options(catalog, warnings)
        options.add_options(entries)
        options.action_first()
        if catalog:
            hint.update("Enter inserts the skill into your draft. Esc closes the list.")
        else:
            hint.update("No local skills found. Check your skill directories. Esc closes the list.")
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
