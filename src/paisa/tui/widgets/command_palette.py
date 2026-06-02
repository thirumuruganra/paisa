from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static


@dataclass(frozen=True)
class PaletteCommand:
    id: str
    label: str
    description: str
    query: str | None = None


class CommandPaletteModal(ModalScreen[PaletteCommand | None]):
    BINDINGS = [("escape", "cancel", "Close"), ("enter", "submit", "Run")]

    def __init__(self, commands: list[PaletteCommand]) -> None:
        super().__init__()
        self._commands = commands
        self._visible_commands: list[PaletteCommand] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="command-palette-modal"):
            yield Static("Command Palette", classes="section-title")
            yield Input(placeholder="Type a command or merchant name", id="command-palette-input")
            yield OptionList(id="command-palette-options")
            yield Static(
                "Enter runs highlighted command. Escape closes. Any free text becomes Search merchants.",
                id="command-palette-help",
                classes="placeholder-copy",
            )

    def on_mount(self) -> None:
        self._refresh_options("")
        self.query_one("#command-palette-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self.dismiss(self._current_command())

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command-palette-input":
            self._refresh_options(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-palette-input":
            self.action_submit()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "command-palette-options":
            self.dismiss(self._command_at(event.option_index))

    def _refresh_options(self, query: str) -> None:
        normalized_query = query.strip()
        lowered = normalized_query.lower()
        visible: list[PaletteCommand] = []

        for command in self._commands:
            haystack = f"{command.label} {command.description}".lower()
            if not lowered or lowered in haystack:
                visible.append(command)

        if normalized_query and not visible:
            visible.append(
                PaletteCommand(
                    id="search-merchants",
                    label=f"Search merchants: {normalized_query}",
                    description="Open Ledger and prefill merchant search",
                    query=normalized_query,
                )
            )

        self._visible_commands = visible
        options = self.query_one("#command-palette-options", OptionList)
        options.clear_options()
        options.add_options([f"{command.label}\\n{command.description}" for command in visible])

    def _current_command(self) -> PaletteCommand | None:
        options = self.query_one("#command-palette-options", OptionList)
        if options.option_count == 0:
            return None
        highlighted = options.highlighted
        if highlighted is None or highlighted < 0:
            return self._visible_commands[0]
        return self._command_at(highlighted)

    def _command_at(self, index: int) -> PaletteCommand | None:
        if index < 0 or index >= len(self._visible_commands):
            return None
        return self._visible_commands[index]