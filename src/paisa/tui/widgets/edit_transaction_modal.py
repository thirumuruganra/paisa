from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from paisa.models import LedgerTransaction


@dataclass(frozen=True)
class EditTransactionResult:
    transaction_id: str
    merchant_name: str
    category_id: int | None
    new_category_name: str | None


class EditTransactionModal(ModalScreen[EditTransactionResult | None]):
    BINDINGS = [
        Binding("enter", "save", show=False),
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, transaction: LedgerTransaction, categories: list[tuple[int, str]]) -> None:
        super().__init__()
        self.transaction = transaction
        self.categories = categories

    def compose(self) -> ComposeResult:
        category_options = [(name, category_id) for category_id, name in self.categories]
        with Vertical(id="edit-transaction-modal"):
            yield Static("Edit Transaction", classes="section-title")
            yield Static(self.transaction.narration, id="edit-narration")
            yield Input(value=self.transaction.merchant_name, placeholder="Merchant name", id="edit-merchant")
            yield Select(category_options, prompt="Existing category", id="edit-category-select")
            yield Input(
                placeholder="Or create a new category",
                id="edit-new-category",
            )
            yield Static("", id="edit-error")
            with Horizontal(id="edit-actions"):
                yield Button("Cancel", id="edit-cancel")
                yield Button("Save", id="edit-save", variant="primary")

    def on_mount(self) -> None:
        select = self.query_one("#edit-category-select", Select)
        category_id = next(
            (category_id for category_id, name in self.categories if name == self.transaction.category_name),
            Select.BLANK,
        )
        select.value = category_id
        self.query_one("#edit-merchant", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-cancel":
            self.dismiss(None)
            return
        if event.button.id == "edit-save":
            self._save()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"edit-merchant", "edit-new-category"}:
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self._save()

    def _save(self) -> None:
        merchant_name = self.query_one("#edit-merchant", Input).value.strip()
        new_category_name = self.query_one("#edit-new-category", Input).value.strip()
        selected_category = self.query_one("#edit-category-select", Select).value

        if not merchant_name:
            self._set_error("Merchant name cannot be empty.")
            return
        if not new_category_name and selected_category == Select.BLANK:
            self._set_error("Pick a category or create a new one.")
            return

        category_id = None if selected_category == Select.BLANK else int(selected_category)
        self.dismiss(
            EditTransactionResult(
                transaction_id=self.transaction.id,
                merchant_name=merchant_name,
                category_id=category_id,
                new_category_name=new_category_name or None,
            )
        )

    def _set_error(self, message: str) -> None:
        self.query_one("#edit-error", Static).update(message)
