from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class SummaryCard(Static):
    def __init__(self, title: str, value: str = "Rs 0.00", **kwargs) -> None:
        super().__init__(classes="summary-card", **kwargs)
        self.title = title
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title, classes="summary-card-title")
            yield Static(self.value, classes="summary-card-value")

    def set_value(self, value: str) -> None:
        self.value = value
        self.query_one(".summary-card-value", Static).update(value)
