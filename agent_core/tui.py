from collections.abc import Iterator
from textual.app import App, ComposeResult, SystemCommand
from textual.screen import Screen
from textual.widgets import OptionList, Select, Static, TextArea
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.binding import Binding
from textual import work
from functools import partial
from importlib.resources import files
from pathlib import Path
from inspect import isawaitable
import os
import re
from threading import Event
from copy import deepcopy
import asyncio
from http.client import HTTPException

from agent_core.loop import run_agent, DEFAULT_MAX_TURNS
from agent_core.approval_screen import ShellApprovalScreen
from agent_core.file_approval_screen import FileApprovalScreen
from agent_core.memory.session import load_session_state, save_session
from agent_core.memory.recovery import (
    RECOVERY_NOTICE, has_interrupted_tools, load_recovery_session,
    new_session_path, prepare_recovery_messages,
)
from agent_core.shell_sandbox import validate_workspace_root
from agent_core.workspace_screen import WorkspaceScreen
from agent_core.session_picker import SessionPicker
from agent_core.slash_commands import COMMANDS, CommandMenu
from agent_core.model_client import DEFAULT_MODEL, QWEN_Q4_MODEL
from agent_core.model_discovery import list_ollama_models
from agent_core.edit_approval_screen import EditApprovalScreen
from agent_core.sensitive_approval_screen import SensitiveApprovalScreen
from agent_core.instructions import build_system_prompt
from agent_core.skills import prepare_skill_prompt
from agent_core.tools.registry import TOOL_REGISTRY
from agent_core.web_approval_screen import WebApprovalScreen
from agent_core.fetch_approval_screen import FetchApprovalScreen
from agent_core.conversation_message import ConversationMessage
from agent_core.image_preview import ImagePreview
from agent_core.greeting import GreetingPortrait
from agent_core.wal_theme import load_wal_theme
from agent_core.prompt_editor import PromptEditor
from agent_core.reasoning_selector import ReasoningSelector
from agent_core.permission_selector import PermissionSelector
from agent_core.turn_selector import TurnSelector
from agent_core.permissions import PermissionMode, permission_instructions


class OceanusApp(App):
    """ A terminal interface (TUI) for Oceanus """

    TITLE = "Oceanus"
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (84, "-wide")]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (22, "-regular-height")]

    BINDINGS = [
        Binding("ctrl+x", "cancel_task", "Cancel task", priority=True),
        Binding("escape", "interrupt_conversation", "Cancel task", show=False),
        ("ctrl+o", "open_session", "Open session"),
        ("ctrl+s", "save_session", "Save session"),
        ("ctrl+q", "quit", "Quit")
    ]

    # Style
    CSS = """
    Screen {
        layout: vertical;
    }

    #top-line {
        height: 1;
        margin: 1 2 0 2;
    }
    #brand { width: auto; color: $text-accent; text-style: bold; }
    #workspace-path {
        width: 1fr; height: 1; padding-left: 2; color: $text-muted;
        text-overflow: ellipsis; text-wrap: nowrap;
    }
    #transcript-area { layers: greeting default; }
    #conversation.opening { layer: greeting; }
    #conversation { height: 1fr; padding: 1 2; }
    #conversation > ConversationMessage { width: 1fr; }
    #activity { height: auto; max-height: 2; color: $text-muted; margin: 0 2; }
    #composer {
        height: auto; min-height: 3; padding: 1; margin: 0 1;
        background: $surface;
    }
    #prompt-mark { width: 2; height: 1; color: $text-accent; text-style: bold; }
    #task-input, #task-input:focus {
        width: 1fr; height: auto; min-height: 1; max-height: 6;
        padding: 0; border: none; background: $surface;
    }
    #model-controls { height: 1; margin: 0 1; }
    #model-controls Select { height: 1; }
    #model-controls SelectCurrent {
        border: none; background: transparent; padding: 0 1;
    }
    #model-controls Select:focus > SelectCurrent { background: $panel; }
    #model-controls SelectOverlay { min-width: 32; constrain: inside inside; }
    #model-select { width: 3fr; color: $text-accent; }
    #reasoning-select { width: 2fr; }
    #permission-select { width: 2fr; color: $accent; }
    #turn-select { width: 14; }
    Screen.-narrow #model-controls {
        layout: grid; height: 2; grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 1 1;
    }
    Screen.-narrow #model-controls Select { width: 1fr; }
    #shortcuts { height: 1; margin: 0 2; color: $text-muted; }
    #command-menu {
        display: none; dock: bottom; height: auto; max-height: 100%; margin: 0 1;
        padding: 0 1; border: none; background: $panel;
    }
    ModalScreen > Vertical > Static:first-child {
        border-left: thick $primary;
        padding-left: 1;
        margin-bottom: 2;
        color: $text-accent;
        text-style: bold;
    }

    ModalScreen > Vertical > VerticalScroll {
        padding: 0 1;
    }

    ModalScreen > Vertical > VerticalScroll > Static {
        margin-bottom: 1;
    }

    ModalScreen > Vertical > OptionList {
        margin: 1 0;
    }

    ModalScreen > Vertical > OptionList > .option-list--option {
        padding: 0 1 1 1;
    }

    ModalScreen.-short > Vertical {
        height: 100%;
        padding: 0 2;
    }

    #pending-prompts {
        display: none;
        height: auto;
        max-height: 5;
        border-top: solid $primary;
        padding: 0 2;
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
        self.command_selector: Select | None = None
        self.cancel_event = Event()
        self.pending_messages: list[dict] = []
        self.request_previews: dict[Path, ImagePreview] = {}
        # The UI owns the queue. The worker only checks this flag between tool batches
        self.followup_event = Event()
        self.conversation_history: list[dict] = []
        self.active_messages: list[dict] | None = None
        self.workspace = Path.cwd().resolve()
        self.session_path = new_session_path()
        self.session_state_saved = False
        self.coding_prompt = (
            files("agents.coding")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )
        

    def compose(self) -> ComposeResult:
        """ Supplied widgets on screen """
        with Horizontal(id="top-line"):
            yield Static("❯_ Oceanus", id="brand", markup=False)
            workspace_label = Static(str(self.workspace), id="workspace-path", markup=False)
            workspace_label.tooltip = str(self.workspace)
            yield workspace_label
        with Vertical(id="transcript-area"):
            with VerticalScroll(id="conversation", classes="opening"):
                yield GreetingPortrait()
            yield CommandMenu(id="command-menu")
        yield Static("", id="activity", markup=False)
        with VerticalScroll(id="pending-prompts") as pending:
            pending.border_title = "Queued"
            yield Static("", id="pending-text", markup=False)
        with Horizontal(id="composer"):
            yield Static("›", id="prompt-mark")
            yield PromptEditor(disabled=True, id="task-input")
        with Horizontal(id="model-controls"):
            yield Select(
                [("GPT-4.1", DEFAULT_MODEL)], value=self.model,
                allow_blank=False, disabled=True, compact=True, id="model-select",
            )
            yield ReasoningSelector(id="reasoning-select", compact=True)
            yield PermissionSelector(id="permission-select", compact=True)
            yield TurnSelector(id="turn-select", compact=True)
        yield Static("/ commands   $ skills   Shift+Enter newline", id="shortcuts", markup=False)

    async def on_mount(self) -> None:
        # Runs after interface mounts
        self.query_one(PromptEditor).command_menu = self.query_one(CommandMenu)
        for control in self.query("#model-controls Select"):
            self.watch(control, "expanded", partial(self.selector_toggled, control), init=False)
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

        available = {value for _, value in local_options}
        self.model = local_options[0][1] if local_options else DEFAULT_MODEL
        for preferred in (QWEN_Q4_MODEL, "ollama_chat/qwen2.5:7b"):
            if preferred in available:
                self.model = preferred
                break

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
        attachments=None,
    ) -> None:
        conversation = self.query_one("#conversation", VerticalScroll)
        if conversation.has_class("opening"):
            conversation.remove_class("opening")
            conversation.remove_children(GreetingPortrait)
        follow_output = conversation.is_vertical_scroll_end
        if kind == "user":
            self.request_previews.clear()
        entry = ConversationMessage(
            kind, message, heading=heading, attachments=attachments, workspace=self.workspace,
            request_previews=self.request_previews,
        )
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
        self.write_message(text, kind="user", heading=heading, attachments=attachments)

    def selector_toggled(self, selector: Select, expanded: bool) -> None:
        if expanded or self.command_selector is not selector:
            return
        self.command_selector = None
        self.call_after_refresh(self.focus_after_selection, selector)

    def focus_after_selection(self, selector: Select) -> None:
        if self.focused is selector:
            self.query_one(PromptEditor).focus()

    def get_system_commands(self, screen: Screen) -> Iterator[SystemCommand]:
        for command in super().get_system_commands(screen):
            if command.title not in ("Maximize", "Screenshot"):
                yield command

    def command_descriptions(self) -> dict[str, str]:
        commands = COMMANDS.copy()
        for command in self.get_system_commands(self.screen):
            commands[command.title.lower()] = command.help
        return commands

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        editor = event.text_area
        if not isinstance(editor, PromptEditor) or editor.command_menu is None:
            return
        menu = editor.command_menu
        if re.fullmatch(r"/[a-z]*", editor.text):
            menu.show_matches(editor.text[1:], self.command_descriptions())
        elif re.fullmatch(r"\$(?:[a-z][a-z0-9_-]*)?", editor.text):
            menu.show_skill_matches(editor.text[1:])
        else:
            menu.display = False

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if isinstance(event.option_list, CommandMenu) and event.option.id:
            event.stop()
            editor = self.query_one(PromptEditor)
            if not event.option_list.display:
                return
            if event.option_list.skill_prefix is not None:
                editor.complete_skill(event.option.id)
            elif re.fullmatch(r"/[a-z]*", editor.text):
                await self.invoke_prompt_command(event.option.id)

    async def invoke_prompt_command(self, name: str) -> None:
        editor = self.query_one(PromptEditor)
        if editor.disabled or editor.paste_pending or len(self.screen_stack) != 1:
            return
        system_actions = {command.title.lower(): command.callback for command in self.get_system_commands(self.screen)}
        if name not in COMMANDS and name not in system_actions:
            self.notify(f"Unknown command /{name}. Type / to see the commands.")
            return
        editor.load_text("")
        self.query_one(CommandMenu).display = False
        editor.focus()
        if name in ("model", "reasoning", "permissions", "turns"):
            selector_id = {"permissions": "permission", "turns": "turn"}.get(name, name)
            selector = self.query_one(f"#{selector_id}-select", Select)
            if selector.disabled:
                reason = "Wait for the current task to finish." if self.busy else str(selector.tooltip)
                self.notify(reason)
            else:
                self.command_selector = selector
                selector.focus()
                selector.action_show_overlay()
            return
        actions = {
            "exit": self.action_quit, "continue": self.action_continue_session,
            "open": self.action_open_session, "save": self.action_save_session,
            "cancel": self.action_cancel_task, "skills": editor.action_choose_skill,
            "palette": self.action_command_palette,
        }
        actions.update(system_actions)
        result = actions[name]()
        if isawaitable(result):
            await result

    async def on_prompt_editor_submitted(self, event: PromptEditor.Submitted) -> None:
        event.stop()
        task_input = self.query_one("#task-input", PromptEditor)
        task = event.text

        if task_input.disabled or task_input.read_only or task_input.paste_pending:
            return
        # Keep image-only prompts and preserve the user's exact text
        if not task.strip() and not event.attachments:
            return
        if not event.attachments and task.strip() == "$":
            self.query_one(CommandMenu).show_skill_matches("")
            return
        if task.lstrip().startswith("/"):
            token, *arguments = task.split(maxsplit=1)
            if (arguments or event.attachments) and token[1:] in self.command_descriptions():
                self.notify(f"Enter {token} on its own. It takes no arguments or images.", severity="warning")
                return
        if not event.attachments and re.fullmatch(r"/[a-z]*", task.strip()):
            prefix = task.strip()[1:]
            selected = self.query_one(CommandMenu).selection_name()
            if selected and selected.startswith(prefix):
                await self.invoke_prompt_command(selected)
            elif prefix:
                await self.invoke_prompt_command(prefix)
            else:
                self.query_one(CommandMenu).show_matches("", self.command_descriptions())
            return

        if self.busy and self.cancel_event.is_set():
            self.notify("Wait for cancellation to finish before sending.")
            return
        message = {"role": "user", "content": task}
        if event.attachments:
            message["attachments"] = deepcopy(event.attachments)
        task_input.load_text("")
        if self.busy:
            self.pending_messages.append(message)
            self.followup_event.set()
            self.refresh_pending_prompts()
            return
        self.start_messages([message])

    def refresh_pending_prompts(self) -> None:
        panel = self.query_one("#pending-prompts", VerticalScroll)
        panel.display = bool(self.pending_messages)
        rows = []
        for message in self.pending_messages:
            text = message["content"].strip() or "Image attachment"
            rows.append(text)
        panel.query_one(Static).update("\n".join(rows))
        if rows:
            panel.scroll_end(animate=False)

    def start_messages(self, messages: list[dict]) -> None:
        self.cancel_event.clear()
        self.busy = True
        self.query_one("#model-select", Select).disabled = True
        reasoning_selector = self.query_one(ReasoningSelector)
        reasoning = reasoning_selector.reasoning
        reasoning_selector.set_task_busy(True)
        permission_selector = self.query_one(PermissionSelector)
        permission_mode = permission_selector.mode
        permission_selector.set_task_busy(True)
        turn_selector = self.query_one(TurnSelector)
        max_turns = turn_selector.max_turns
        turn_selector.set_task_busy(True)
        self.sub_title = f"{self.model} | Working..."
        self.query_one("#activity", Static).tooltip = f"Recovery checkpoints: {self.session_path}"
        for message in messages:
            self.write_user_message(message["content"], message.get("attachments"))
        self.query_one(PromptEditor).placeholder = "Queue a follow-up"
        self.query_one("#activity", Static).update("Working…")

        latest = messages[-1]
        self.run_task(
            latest["content"], deepcopy(self.conversation_history), self.model,
            attachments=latest.get("attachments", []), earlier_submissions=messages[:-1],
            reasoning=reasoning,
            permission_mode=permission_mode,
            active_messages=deepcopy(self.active_messages),
            max_turns=max_turns,
        )

    def report_progress(self, message: str) -> None:
        # The loop adds this prefix to intermediate agent replies
        if message.startswith("Agent: "):
            text = message.removeprefix("Agent: ")
            self.call_from_thread(self.write_message, text, kind="assistant")
        elif message.startswith(("This is model turn: ", "Running tool: ")):
            activity = message.removeprefix("This is model turn: ")
            if activity != message:
                activity = f"Model turn {activity}"
            self.call_from_thread(self.show_activity, activity)
        else:
            self.call_from_thread(self.write_message, message, kind="status")

    def show_activity(self, message: str) -> None:
        self.query_one("#activity", Static).update(message)

    def report_tool_result(self, tool_name: str, result: str) -> None:
        self.call_from_thread(
            self.write_message,
            result,
            kind="tool",
            heading=tool_name,
        )

    def report_tool_status(self, tool_name: str, status: str) -> None:
        if status != "success":
            self.call_from_thread(self.write_message, f"{tool_name} · {status}", kind="status")

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

    def request_render_approval(self, url: str) -> bool:
        return self.call_from_thread(self.show_render_approval, url)

    def request_pdf_fetch_approval(self, url: str) -> bool:
        return self.call_from_thread(self.show_pdf_fetch_approval, url)

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
    

    async def show_render_approval(self, url: str) -> bool:
        if self.cancel_event.is_set():
            return False
        decision = await self.push_screen_wait(FetchApprovalScreen(url, rendered=True))
        return decision is True and not self.cancel_event.is_set()

    async def show_pdf_fetch_approval(self, url: str) -> bool:
        if self.cancel_event.is_set():
            return False
        decision = await self.push_screen_wait(FetchApprovalScreen(url, pdf=True))
        return decision is True and not self.cancel_event.is_set()

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
        earlier_submissions: list[dict] | None = None,
        reasoning: str | bool | None = None,
        permission_mode: PermissionMode = PermissionMode.ASK,
        active_messages: list[dict] | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        """ Runs the prompt. Executed in a background thread. """
        if active_messages is None:
            active_messages = deepcopy(history)
        try:
            coding_prompt = build_system_prompt(self.coding_prompt, workspace=self.workspace)
            coding_prompt, skill_notices = prepare_skill_prompt(
                coding_prompt, task, available_tools=TOOL_REGISTRY, workspace=self.workspace,
                earlier_tasks=[item["content"] for item in earlier_submissions or []],
            )
            coding_prompt += permission_instructions(permission_mode)
            for notice in skill_notices:
                self.report_progress(notice)
            # The initial checkpoint includes every queued message, in submission order
            if earlier_submissions:
                history.extend(deepcopy(earlier_submissions))
                active_messages.extend(deepcopy(earlier_submissions))
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
                followups_waiting=self.followup_event.is_set,
                on_tool_status=self.report_tool_status,
                on_search_approval=self.request_search_approval,
                on_edit_approval=self.request_edit_approval,
                on_sensitive_approval=self.request_sensitive_approval,
                on_discovery_approval=self.request_discovery_approval,
                on_web_approval=self.request_web_approval,
                on_fetch_approval=self.request_fetch_approval,
                on_render_approval=self.request_render_approval,
                on_pdf_fetch_approval=self.request_pdf_fetch_approval,
                attachments=attachments,
                reasoning=reasoning,
                permission_mode=permission_mode,
                max_turns=max_turns,
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
        self, message: str | None, history: list[dict] | None, *, failed: bool = False,
        active_messages: list[dict] | None = None,
    ) -> None:
        # Keep submitted attachments and completed tool actions after a failure
        if history is not None:
            self.conversation_history = history
            self.active_messages = active_messages
            self.session_state_saved = False
        if failed or history is None:
            kind = "error"
        else:
            kind = "assistant"

        if message is not None:
            self.write_message(message, kind=kind)

        pending = self.pending_messages
        self.pending_messages = []
        self.followup_event.clear()
        if pending:
            self.refresh_pending_prompts()
            # Only hand off a completed answer or a fully recorded tool batch
            last_reply = history[-1] if history else {}
            answered = (
                last_reply.get("role") == "assistant"
                and not last_reply.get("tool_calls")
                and (last_reply.get("content") or "") == message
            )
            if not failed and not self.cancel_event.is_set() and (message is None or answered):
                self.start_messages(pending)
                return
            for item in pending:
                self.write_message(item["content"], kind="user", heading="Unsent follow-up",
                                   attachments=item.get("attachments"))
        self.busy = False
        self.sub_title = self.model
        self.query_one("#model-select", Select).disabled = False
        self.query_one(ReasoningSelector).set_task_busy(False)
        self.query_one(PermissionSelector).set_task_busy(False)
        self.query_one(TurnSelector).set_task_busy(False)

        task_input = self.query_one("#task-input", PromptEditor)
        task_input.placeholder = "Ask Oceanus anything"
        self.query_one("#activity", Static).update("")
        task_input.disabled = False
        task_input.focus()

    def action_continue_session(self) -> None:
        editor = self.query_one(PromptEditor)
        if self.busy or editor.paste_pending:
            self.notify("Wait for the current operation before switching conversations.")
            return
        workspace = self.workspace.resolve()

        def resume_here(path: Path | None) -> None:
            self.restore_session(path, required_workspace=workspace)

        picker = SessionPicker([], directory=self.session_path.parent, workspace=workspace)
        self.push_screen(picker, resume_here)

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
                "Use /continue to switch conversations here. Open other workspaces from a fresh TUI."
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

    def restore_session(self, path: Path | None, *, required_workspace: Path | None = None) -> None:
        if path is None:
            return

        if self.busy or self.query_one("#task-input", PromptEditor).paste_pending:
            self.notify("Wait for the current operation before opening a session.")
            return

        try:
            path = path.resolve()
            if required_workspace is not None and path == self.session_path and self.conversation_history:
                self.notify("This conversation is already open.")
                return
            history, active_messages, workspace = load_recovery_session(path)
            if not history:
                raise ValueError("This session is empty!")
            if required_workspace is not None:
                if workspace is None or workspace.resolve() != required_workspace:
                    raise ValueError("This conversation belongs to a different workspace.")
                if path != self.session_path and self.conversation_history:
                    self.preserve_outgoing_session()
        except (OSError, ValueError, RuntimeError) as error:
            self.notify(f"Could not open session: {error}", severity="error")
            return

        if workspace is None:
            def choose_workspace(selected: Path | None) -> None:
                if selected is not None:
                    self.apply_session(path, history, selected, active_messages=active_messages)

            self.push_screen(WorkspaceScreen(self.workspace), choose_workspace)
            return

        self.apply_session(path, history, workspace, active_messages=active_messages)

    def preserve_outgoing_session(self) -> None:
        destination = self.session_path
        try:
            saved, active, workspace = load_session_state(destination)
        except FileNotFoundError:
            destination.parent.mkdir(parents=True, exist_ok=True)
        else:
            # An idle terminal may still be displaying an older saved conversation
            if self.session_state_saved and workspace == self.workspace:
                return
            history = self.conversation_history
            local_active = self.active_messages if self.active_messages is not None else history
            if workspace == self.workspace and saved == history and active == local_active:
                return
            # Keep both versions when local work differs from the checkpoint
            destination = destination.with_name(new_session_path().name)
        save_session(
            destination, self.conversation_history, workspace=self.workspace,
            active_messages=self.active_messages, overwrite=False,
        )
        if destination != self.session_path:
            self.notify(f"Saved the local conversation separately at {destination}")

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
        self.session_state_saved = True
        conversation = self.query_one("#conversation", VerticalScroll)
        conversation.remove_children(ConversationMessage)
        self.request_previews.clear()

        # Display saved content without changing the model's history
        tool_names = {}
        for message in history:
            role = message.get("role")
            content = message.get("content")
            if role == "assistant":
                tool_names.clear()
                for call in message.get("tool_calls") or []:
                    function = call.get("function")
                    if isinstance(function, dict) and isinstance(function.get("name"), str):
                        tool_names[call["id"]] = function["name"]
            if isinstance(content, str):
                if role == "user":
                    self.write_user_message(content, message.get("attachments"))
                elif role == "tool":
                    name = tool_names.get(message.get("tool_call_id"))
                    self.write_message(content, kind=role, heading=name)
                elif role == "assistant":
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

        self.session_state_saved = True
        self.write_message(f"Session saved: {self.session_path}")

    def action_interrupt_conversation(self) -> None:
        if len(self.screen_stack) == 1:
            self.action_cancel_task()

    def action_cancel_task(self) -> None:
        if not self.busy or self.cancel_event.is_set():
            return

        self.cancel_event.set()
        self.sub_title = f"{self.model} | Cancelling..."
        self.query_one("#activity", Static).update("Cancelling…")

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
