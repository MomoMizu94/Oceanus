"""Prompt shortcuts for the actions already available in the terminal UI."""

import asyncio
from collections import Counter

from textual import work
from textual.widgets import OptionList
from textual.geometry import Size
from textual.message import Message
from textual.widgets.option_list import Option

from agent_core.skill_picker import skill_options
from agent_core.skills import Skill, discover_skills


# The keys are also used by the prompt menu and its keyboard navigation
COMMANDS = {
    "continue": "Resume a conversation in this workspace",
    "model": "Choose the chat model",
    "reasoning": "Choose a reasoning mode",
    "permissions": "Change tool permissions",
    "turns": "Set the turn limit",
    "skills": "Insert an installed skill",
    "open": "Open a session from any workspace",
    "cancel": "Interrupt the active task",
    "exit": "Exit Oceanus",
}


class CommandMenu(OptionList):
    """Keep keyboard focus in the editor while offering matching actions."""

    skill_prefix: str | None = None
    skill_catalog: tuple[list[Skill], list[str]] | None = None
    skill_names: frozenset[str] = frozenset()

    class SkillsLoaded(Message):
        """The editor can now highlight names from the refreshed catalog."""

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return min(8, super().get_content_height(container, viewport, width))

    def show_matches(self, prefix: str, commands: dict[str, str]) -> None:
        self.skill_prefix = None
        self.clear_options()
        for name, description in commands.items():
            if name.startswith(prefix):
                self.add_option(Option(f"/{name:<13} {description}", id=name))
        self.display = self.option_count > 0
        if self.display:
            self.highlighted = 0

    def show_skill_matches(self, prefix: str) -> None:
        self.skill_prefix = prefix
        self.display = True
        self.clear_options()
        if self.skill_catalog is None:
            self.add_option(Option("Loading skills…", disabled=True))
            return
        catalog, warnings = self.skill_catalog
        entries = skill_options(catalog, warnings, prefix)
        if not entries:
            hint = "No matching skills" if prefix else "No installed skills found"
            entries.append(Option(hint, disabled=True))
        self.add_options(entries)
        self.action_first()

    @work(exclusive=True)
    async def load_skills(self) -> None:
        try:
            self.skill_catalog = await asyncio.to_thread(discover_skills)
        except (OSError, ValueError) as error:
            self.skill_catalog = ([], [f"Could not load skills: {error}"])
        counts = Counter(skill.name for skill in self.skill_catalog[0])
        self.skill_names = frozenset(name for name, count in counts.items() if count == 1)
        self.post_message(self.SkillsLoaded())
        if self.skill_prefix is not None and self.display:
            self.show_skill_matches(self.skill_prefix)

    def selection_name(self) -> str | None:
        if self.display and self.highlighted is not None:
            return self.get_option_at_index(self.highlighted).id
        return None
