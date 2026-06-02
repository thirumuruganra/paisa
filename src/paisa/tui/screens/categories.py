from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Static

from paisa.db.queries import delete_category, ensure_category, list_category_summaries, rename_category
from paisa.models import CategorySummary


class CategoriesView(Static):
    def __init__(self, db_path: Path, **kwargs) -> None:
        super().__init__(classes="screen-view", **kwargs)
        self.db_path = db_path
        self._categories: list[CategorySummary] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Categories", classes="section-title")
            yield Static(
                "Add categories here, rename selected labels, and delete only unused ones. Misc stays locked because imports fall back to it.",
                id="categories-status",
                classes="placeholder-copy placeholder-box",
            )
            with Horizontal(id="categories-toolbar"):
                yield Input(placeholder="Category name", id="categories-name")
                yield Button("Add", id="categories-add", variant="primary")
                yield Button("Rename Selected", id="categories-rename")
                yield Button("Delete Selected", id="categories-delete")
                yield Button("Refresh", id="categories-refresh")
            yield DataTable(id="categories-table")
            yield Static("", id="categories-empty", classes="placeholder-copy placeholder-box")

    def on_mount(self) -> None:
        table = self.query_one("#categories-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Category", "Transactions", "Rules", "Examples")
        self.load_categories()

    def refresh_view(self) -> None:
        self.load_categories()

    @work(exclusive=True)
    async def load_categories(self) -> None:
        self._categories = await list_category_summaries(self.db_path)
        self._render_categories()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "categories-name":
            self._handle_add()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "categories-add":
            self._handle_add()
        elif event.button.id == "categories-rename":
            self.run_worker(self._rename_selected(), exclusive=True)
        elif event.button.id == "categories-delete":
            self.run_worker(self._delete_selected(), exclusive=True)
        elif event.button.id == "categories-refresh":
            self.load_categories()

    def _handle_add(self) -> None:
        self.run_worker(self._add_category(), exclusive=True)

    async def _add_category(self) -> None:
        name = self.query_one("#categories-name", Input).value.strip()
        if not name:
            self._set_status("Category name cannot be empty.", error=True)
            return

        await ensure_category(self.db_path, name)
        self.query_one("#categories-name", Input).value = ""
        self._set_status(f"Category ready: {name}")
        await self._reload_after_change()

    async def _rename_selected(self) -> None:
        selected = self._selected_category()
        if selected is None:
            self._set_status("No category available to rename.", error=True)
            return

        new_name = self.query_one("#categories-name", Input).value.strip()
        if not new_name:
            self._set_status("Type a new category name before renaming.", error=True)
            return

        try:
            await rename_category(self.db_path, selected.category_id, new_name)
        except (LookupError, ValueError) as exc:
            self._set_status(str(exc), error=True)
            return

        self.query_one("#categories-name", Input).value = ""
        self._set_status(f"Renamed {selected.category_name} to {new_name}.")
        await self._reload_after_change()

    async def _delete_selected(self) -> None:
        selected = self._selected_category()
        if selected is None:
            self._set_status("No category available to delete.", error=True)
            return

        try:
            await delete_category(self.db_path, selected.category_id)
        except (LookupError, ValueError) as exc:
            self._set_status(str(exc), error=True)
            return

        self._set_status(f"Deleted {selected.category_name}.")
        await self._reload_after_change()

    async def _reload_after_change(self) -> None:
        self._categories = await list_category_summaries(self.db_path)
        self._render_categories()
        self._refresh_dependents()

    def _render_categories(self) -> None:
        table = self.query_one("#categories-table", DataTable)
        table.clear(columns=False)
        for category in self._categories:
            table.add_row(
                category.category_name,
                str(category.transaction_count),
                str(category.rule_count),
                str(category.example_count),
            )

        empty = self.query_one("#categories-empty", Static)
        has_rows = bool(self._categories)
        table.display = has_rows
        empty.display = not has_rows
        if not has_rows:
            empty.update("No categories yet. Add one above to start organizing debits.")
        else:
            empty.update("")

    def _selected_category(self) -> CategorySummary | None:
        if not self._categories:
            return None

        table = self.query_one("#categories-table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index < 0:
            row_index = 0
        if row_index >= len(self._categories):
            row_index = len(self._categories) - 1
        return self._categories[row_index]

    def _refresh_dependents(self) -> None:
        for selector, method_name in (
            ("#view-overview", "refresh_view"),
            ("#view-expenses", "refresh_view"),
            ("#view-ledger", "load_transactions"),
        ):
            try:
                view = self.app.query_one(selector)
            except NoMatches:
                continue
            getattr(view, method_name)()

    def _set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one("#categories-status", Static)
        status.update(message)
        status.styles.color = "#ff6b6b" if error else "#9aa4b2"