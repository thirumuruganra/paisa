from __future__ import annotations

from pathlib import Path

from textual import events
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Static

from paisa.categorization import CategorizationService
from paisa.db.queries import ensure_category, list_categories, list_transactions, update_transaction_details
from paisa.models import CategorizationPrompt, CategorizationResolution, LedgerTransaction, TransactionInput
from paisa.tui.widgets import CategorizationReviewModal, EditTransactionModal, EditTransactionResult


def _format_amount(transaction: LedgerTransaction) -> str:
    sign = "+" if transaction.type == "CREDIT" else "-"
    return f"{sign}Rs {transaction.amount:,.2f}"


class LedgerView(Static):
    BINDINGS = [Binding("r", "ai_recategorize_visible", "AI Recat Visible")]

    def __init__(self, db_path: Path, **kwargs) -> None:
        super().__init__(classes="screen-view", **kwargs)
        self.db_path = db_path
        self._transactions: list[LedgerTransaction] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Ledger", classes="section-title")
            with Horizontal(id="ledger-toolbar"):
                yield Input(placeholder="Search merchant, narration, category, or bank", id="ledger-search")
                yield Button("AI Recategorize Visible", id="ledger-ai-recategorize")
            yield Static(
                "Filter to Misc, then run AI Recategorize Visible or press r. Review results and manually fix mistakes.",
                id="ledger-status",
                classes="placeholder-copy",
            )
            yield DataTable(id="ledger-table")
            yield Static("No transactions imported yet.", id="ledger-empty")

    def on_mount(self) -> None:
        table = self.query_one("#ledger-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Date", "Merchant", "Narration", "Category", "Type", "Amount", "Bank")
        self.load_transactions()
        table.focus()

    @work(exclusive=True)
    async def load_transactions(self) -> None:
        self._transactions = await list_transactions(self.db_path)
        self._apply_filter(self.query_one("#ledger-search", Input).value if self.is_mounted else "")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "ledger-search":
            self._apply_filter(event.value)

    def on_key(self, event: events.Key) -> None:
        search = self.query_one("#ledger-search", Input)
        if event.key == "escape" and search.has_focus:
            self.query_one("#ledger-table", DataTable).focus()
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ledger-ai-recategorize":
            self.action_ai_recategorize_visible()

    def action_ai_recategorize_visible(self) -> None:
        self.run_worker(self._recategorize_visible_transactions(), exclusive=True)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "ai_recategorize_visible" and self.app.active_view != "ledger":
            return False
        return super().check_action(action, parameters)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "ledger-table":
            return
        await self._open_edit_modal(event.cursor_row)

    async def _open_edit_modal(self, row_index: int) -> None:
        visible_transactions = self._visible_transactions(self.query_one("#ledger-search", Input).value)
        if not visible_transactions or row_index >= len(visible_transactions):
            return

        transaction = visible_transactions[row_index]
        categories = await list_categories(self.db_path)
        await self.app.push_screen(
            EditTransactionModal(transaction, categories),
            callback=self._handle_edit_result,
        )

    def _handle_edit_result(self, result: EditTransactionResult | None) -> None:
        if result is None:
            return

        self.run_worker(self._apply_edit_result(result), exclusive=True)

    async def _apply_edit_result(self, result: EditTransactionResult) -> None:
        category_id = result.category_id
        if result.new_category_name:
            category_id = await ensure_category(self.db_path, result.new_category_name)

        if category_id is None:
            return

        original_transaction = next(
            (transaction for transaction in self._transactions if transaction.id == result.transaction_id),
            None,
        )

        await update_transaction_details(
            self.db_path,
            transaction_id=result.transaction_id,
            merchant_name=result.merchant_name,
            category_id=category_id,
        )
        if original_transaction is not None:
            await CategorizationService(self.db_path).remember_feedback(
                merchant_name=result.merchant_name,
                narration=original_transaction.narration,
                category_id=category_id,
                transaction_id=result.transaction_id,
            )
        search_term = self.query_one("#ledger-search", Input).value
        self._transactions = await list_transactions(self.db_path)
        self._apply_filter(search_term, selected_transaction_id=result.transaction_id)
        self._refresh_dependents()

    async def _recategorize_visible_transactions(self) -> None:
        visible_transactions = self._visible_transactions(self.query_one("#ledger-search", Input).value)
        debit_transactions = [transaction for transaction in visible_transactions if transaction.type == "DEBIT"]
        if not debit_transactions:
            self._set_status("No visible debit transactions to recategorize.", error=True)
            return

        categorizer = CategorizationService(self.db_path)
        updated_count = 0
        for transaction in debit_transactions:
            decision = await categorizer.categorize_transaction(
                self._as_transaction_input(transaction),
                resolver=self._resolve_categorization_prompt,
            )
            if decision.category_name == transaction.category_name:
                continue

            await update_transaction_details(
                self.db_path,
                transaction_id=transaction.id,
                merchant_name=transaction.merchant_name,
                category_id=decision.category_id,
            )
            updated_count += 1

        self._transactions = await list_transactions(self.db_path)
        self._apply_filter(self.query_one("#ledger-search", Input).value)
        self._refresh_dependents()
        if updated_count == 0:
            self._set_status("AI found no better category changes for the visible debit transactions.")
            return
        self._set_status(f"AI recategorized {updated_count} visible debit transaction(s). Review ledger and fix any mistakes.")

    async def _resolve_categorization_prompt(
        self,
        prompt: CategorizationPrompt,
    ) -> CategorizationResolution | None:
        return await self.app.push_screen_wait(CategorizationReviewModal(prompt))

    def _refresh_dependents(self) -> None:
        for selector in ("#view-overview", "#view-expenses", "#view-categories"):
            try:
                self.app.query_one(selector).refresh_view()
            except NoMatches:
                continue

    def _set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one("#ledger-status", Static)
        status.update(message)
        status.styles.color = "#ff6b6b" if error else "#9aa4b2"

    def _apply_filter(self, term: str, selected_transaction_id: str | None = None) -> None:
        normalized = term.strip().lower()
        visible = self._visible_transactions(term)

        table = self.query_one("#ledger-table", DataTable)
        table.clear(columns=False)
        selected_row_index = 0
        for transaction in visible:
            if selected_transaction_id is not None and transaction.id == selected_transaction_id:
                selected_row_index = len(table.rows)
            table.add_row(
                transaction.date.isoformat(),
                transaction.merchant_name,
                transaction.narration,
                transaction.category_name,
                transaction.type,
                _format_amount(transaction),
                transaction.bank,
            )

        empty = self.query_one("#ledger-empty", Static)
        if self._transactions:
            empty.update("No search results." if normalized and not visible else "")
        else:
            empty.update("No transactions imported yet.")
        empty.display = not visible
        table.display = bool(visible)
        if visible:
            table.move_cursor(row=selected_row_index, column=0, animate=False, scroll=True)

    def _visible_transactions(self, term: str) -> list[LedgerTransaction]:
        normalized = term.strip().lower()
        if not normalized:
            return self._transactions
        return [
            transaction
            for transaction in self._transactions
            if normalized in transaction.merchant_name.lower()
            or normalized in transaction.narration.lower()
            or normalized in transaction.category_name.lower()
            or normalized in transaction.bank.lower()
        ]

    def _as_transaction_input(self, transaction: LedgerTransaction) -> TransactionInput:
        return TransactionInput(
            id=transaction.id,
            bank=transaction.bank,
            date=transaction.date,
            narration=transaction.narration,
            merchant_name=transaction.merchant_name,
            amount=transaction.amount,
            type=transaction.type,
        )
