from copy import deepcopy
import asyncio

from textual.widgets import TextArea
from textual.events import Key
from textual.message import Message
from textual.widgets.text_area import Edit, EditResult
from textual import work
from textual.binding import Binding

from agent_core.prompt_image_edit import ImageMarker, PromptEdit
from agent_core.clipboard_images import read_clipboard_image
from agent_core.skill_picker import SkillPicker
from agent_core.slash_commands import CommandMenu
from agent_core.image_attachments import MAX_IMAGES_PER_MESSAGE, save_image_bytes


class PromptEditor(TextArea):
    """ Edit a multiline prompt before sending """

    class Submitted(Message):
        """ Carry a copy of the prompt when enter is pressed """

        def __init__(self, text: str, attachments: list[dict] | None = None) -> None:
            super().__init__()
            self.text = text

            if attachments is None:
                self.attachments = []
            else:
                self.attachments = deepcopy(attachments)

    BINDINGS = [
        Binding("ctrl+v", "paste_image", "Paste image"),
        Binding("f2", "choose_skill", "Skills"),
    ]

    DEFAULT_CSS = """
    PromptEditor {
        height: 7;
        min-height: 3;
        background: $surface;
        color: $foreground;
    }
    """

    def __init__(self, *, id: str | None = None, disabled: bool = False) -> None:
        self.draft_revision = 0
        self.paste_pending = False
        self.image_markers: list[ImageMarker] = []
        self.command_menu: CommandMenu | None = None
        self.next_image_number = 1
        super().__init__(
            id=id,
            disabled=disabled,
            theme="css",
            # Wrap visually without inserting extra newlines
            soft_wrap=True,
            # Let tab move focus to another control
            tab_behavior="focus",
            show_line_numbers=False,
            highlight_cursor_line=False,
            placeholder="Ask Oceanus anything",
        )

    def edit(self, edit: Edit) -> EditResult:
        # Route ordinary typing and deletion through marker-aware edits
        if not isinstance(edit, PromptEdit):
            edit = PromptEdit(edit)

        result = super().edit(edit)
        self.draft_revision += 1
        return result

    def load_text(self, text: str) -> None:
        # Loaded text starts a fresh draft with no attachment links
        self.image_markers = []
        self.next_image_number = 1
        super().load_text(text)
        self.draft_revision += 1

    def undo(self) -> None:
        super().undo()
        self.draft_revision += 1

    def redo(self) -> None:
        super().redo()
        self.draft_revision += 1

    def insert_image(self, attachment: dict) -> None:
        if self.disabled or self.read_only:
            return

        label = f"[Image {self.next_image_number}]"
        start, end = self.selection
        edit = Edit(label, start, end, maintain_selection_offset=False)

        self.edit(PromptEdit(edit, attachment))
        self.next_image_number += 1

    def get_attachments(self) -> list[dict]:
        attachments = []

        for marker in self.image_markers:
            attachment = deepcopy(marker.attachment)
            attachment["label"] = self.text[marker.start:marker.end]
            attachments.append(attachment)

        return attachments

    def action_choose_skill(self) -> None:
        if self.disabled or self.read_only or self.paste_pending:
            return
        self.app.push_screen(SkillPicker(), self.insert_chosen_skill)

    def insert_chosen_skill(self, name: str | None) -> None:
        if name is not None and not self.disabled and not self.read_only:
            # Keep the draft's first line intact, including Markdown fences
            separator = "\n" if self.text else " "
            self.insert(f"${name}{separator}", (0, 0))
        self.focus()

    def complete_skill(self, name: str) -> None:
        if self.disabled or self.read_only or self.paste_pending:
            return
        if self.text.startswith("$") and name.startswith(self.text[1:]):
            self.replace(f"${name} ", (0, 0), (0, len(self.text)), maintain_selection_offset=False)
            self.focus()

    def action_paste_image(self) -> None:
        if self.disabled or self.read_only or self.paste_pending:
            return

        self.paste_pending = True
        self.paste_image(self.draft_revision, self.selection)

    def paste_target_matches(self, revision: int, selection) -> bool:
        if not self.is_mounted or self.disabled or self.read_only:
            return False

        if revision != self.draft_revision or selection != self.selection:
            self.notify(
                "Prompt changed during image paste. Paste again.",
                severity="warning",
            )
            return False

        return True

    def on_key(self, event: Key) -> None:
        menu = self.command_menu
        if (menu is not None and menu.display and menu.skill_prefix is not None
                and self.text == "$" + menu.skill_prefix and event.key in ("enter", "tab")):
            name = menu.selection_name()
            if name or menu.skill_catalog is None or self.text == "$" or event.key == "tab":
                event.stop()
                event.prevent_default()
                if name:
                    self.complete_skill(name)
                return
        if menu is not None and menu.display and event.key in ("up", "down", "tab", "escape"):
            event.stop()
            event.prevent_default()
            if event.key == "up":
                menu.action_cursor_up()
            elif event.key == "down":
                menu.action_cursor_down()
            elif event.key == "escape":
                menu.display = False
            else:
                name = menu.selection_name()
                if name and menu.skill_prefix is None:
                    self.load_text("/" + name)
                    self.move_cursor((0, len(self.text)))
            return
        if event.key not in ("enter", "shift+enter"):
            return

        # Prevent TextArea from handling these keys
        event.stop()
        event.prevent_default()

        if self.disabled or self.read_only:
            return

        if event.key == "enter" and self.paste_pending:
            self.notify("Wait for the image paste to finish before sending.")
            return
        if event.key == "shift+enter":
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
        elif self.text.strip():
            attachments = self.get_attachments()
            self.post_message(self.Submitted(self.text, attachments))


    @work(group="image-paste")
    async def paste_image(self, revision: int, selection) -> None:
        try:
            # Clipboard access can wait on another application
            data = await asyncio.to_thread(read_clipboard_image)

            if not self.paste_target_matches(revision, selection):
                return

            if data is None:
                self.notify(
                    "No image on the clipboard. Use terminal paste for text."
                )
                return

            start_location, end_location = sorted(selection)
            start = self.document.get_index_from_location(start_location)
            end = self.document.get_index_from_location(end_location)

            remaining_images = 0
            for marker in self.image_markers:
                replaces_marker = (
                    start < end
                    and start < marker.end
                    and end > marker.start
                )
                if not replaces_marker:
                    remaining_images += 1

            if remaining_images >= MAX_IMAGES_PER_MESSAGE:
                raise ValueError("A prompt supports at most four images")

            # Save privately before adding a marker that refers to the image
            attachment = await asyncio.to_thread(save_image_bytes, data)

            if self.paste_target_matches(revision, selection):
                self.insert_image(attachment)

        except (OSError, ValueError) as error:
            if self.is_mounted:
                self.notify(str(error), severity="error")
        finally:
            self.paste_pending = False
