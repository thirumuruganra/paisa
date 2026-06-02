from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Footer, Header

from paisa.ai import ChatService
from paisa.config import Settings
from paisa.importers.pipeline import import_statements
from paisa.tui.screens import AskAiView, CategoriesView, ExpensesView, LedgerView, OverviewView
from paisa.tui.widgets import CommandPaletteModal, PaletteCommand


DEFAULT_LOGO = """ 888888ba   .d888888  dP .d88888b   .d888888  
 88    `8b d8'    88  88 88.    "' d8'    88  
a88aaaa8P' 88aaaaa88a 88 `Y88888b. 88aaaaa88a 
 88        88     88  88       `8b 88     88  
 88        88     88  88 d8'   .8P 88     88  
 dP        88     88  dP  Y88888P  88     88  
"""


def _load_logo() -> str:
    logo_path = Path(__file__).resolve().parents[3] / "logo.txt"
    try:
        return logo_path.read_text(encoding="utf-8").rstrip()
    except OSError:
        return DEFAULT_LOGO


class PaisaTuiApp(App[None]):
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("1", "show_overview", "Overview"),
        Binding("2", "show_expenses", "Expenses"),
        Binding("3", "show_ledger", "Ledger"),
        Binding("4", "show_categories", "Categories"),
        Binding("5", "show_ask_ai", "Ask AI"),
        Binding("q", "quit", "Quit"),
        Binding("t", "toggle_theme", "Theme"),
        Binding("ctrl+k", "command_palette", "Palette", priority=True),
    ]

    def __init__(self, settings: Settings, chat_service: ChatService | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.active_view = "overview"
        self.logo_text = _load_logo()
        self.chat_service = chat_service

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ContentSwitcher(id="view-switcher"):
            yield OverviewView(self.settings.db_path, self.logo_text, id="view-overview")
            yield ExpensesView(self.settings.db_path, id="view-expenses")
            yield LedgerView(self.settings.db_path, id="view-ledger")
            yield CategoriesView(self.settings.db_path, id="view-categories")
            yield AskAiView(self.settings.db_path, chat_service=self.chat_service, id="view-ask-ai")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Paisa"
        self.sub_title = str(self.settings.db_path)
        self._show_view("overview")

        # 2. Add this logic to target the clock
        try:
            # Find the HeaderClock widget inside the Header
            header = self.query_one(Header)
            clock = header.query_one("HeaderClock")
            
            # Define the IST update logic
            def get_ist_time():
                # Create a timezone object for UTC +5:30
                ist = timezone(timedelta(hours=5, minutes=30))
                return datetime.now(ist).strftime("%H:%M:%S")

            # Override the clock's render method
            clock.render = get_ist_time
        except Exception:
            # Fallback in case the header clock isn't found
            pass

    def action_show_overview(self) -> None:
        self._show_view("overview")

    def action_show_expenses(self) -> None:
        self._show_view("expenses")

    def action_show_ledger(self) -> None:
        self._show_view("ledger")

    def action_show_categories(self) -> None:
        self._show_view("categories")

    def action_show_ask_ai(self) -> None:
        self._show_view("ask-ai")
        self.query_one("#view-ask-ai").focus_input()

    def action_toggle_theme(self) -> None:
        self.dark = not self.dark

    def action_command_palette(self) -> None:
        self.push_screen(CommandPaletteModal(self._palette_commands()), self._handle_palette_command)

    @work(exclusive=True)
    async def run_manual_import(self) -> None:
        result = await import_statements(
            db_path=self.settings.db_path,
            statements_dir=self.settings.statements_dir,
            password_provider=None,
        )
        self._refresh_views()

        if result.failures:
            self.notify(
                f"Import finished: {result.imported_files} file(s), {result.skipped_duplicates} duplicate(s), {len(result.failures)} failure(s)."
            )
            return

        self.notify(
            f"Import finished: {result.imported_files} file(s), {result.imported_transactions} transaction(s), {result.skipped_duplicates} duplicate(s)."
        )

    def _show_view(self, view_name: str) -> None:
        self.active_view = view_name
        self.query_one(ContentSwitcher).current = f"view-{view_name}"
        self.refresh_bindings()

    def _palette_commands(self) -> list[PaletteCommand]:
        return [
            PaletteCommand("jump-overview", "Jump to Overview", "Open dashboard"),
            PaletteCommand("jump-expenses", "Jump to Expenses", "Open spend analysis"),
            PaletteCommand("jump-ledger", "Jump to Ledger", "Open transaction ledger"),
            PaletteCommand("jump-ask-ai", "Jump to Ask AI", "Open local finance chat"),
            PaletteCommand("manual-import", "Trigger manual import", "Import statement PDFs in the background"),
            PaletteCommand("refresh-dashboard", "Refresh dashboard data", "Reload overview, expenses, categories, and ledger"),
        ]

    def _handle_palette_command(self, command: PaletteCommand | None) -> None:
        if command is None:
            return

        if command.id == "jump-overview":
            self.action_show_overview()
            return
        if command.id == "jump-expenses":
            self.action_show_expenses()
            return
        if command.id == "jump-ledger":
            self.action_show_ledger()
            return
        if command.id == "jump-ask-ai":
            self.action_show_ask_ai()
            return
        if command.id == "refresh-dashboard":
            self._refresh_views()
            self.notify("Refreshed local dashboard views.")
            return
        if command.id == "manual-import":
            self.run_manual_import()
            return
        if command.id == "search-merchants":
            self._search_merchants(command.query or "")

    def _refresh_views(self) -> None:
        self.query_one("#view-overview").refresh_view()
        self.query_one("#view-expenses").refresh_view()
        self.query_one("#view-categories").refresh_view()
        self.query_one("#view-ledger").load_transactions()

    def _search_merchants(self, query: str) -> None:
        self.action_show_ledger()
        ledger = self.query_one("#view-ledger")
        search = ledger.query_one("#ledger-search")
        search.value = query
        search.focus()
