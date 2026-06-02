from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual_plotext import PlotextPlot
from textual.widgets import Button, Static

from paisa.db.queries import get_expense_snapshot
from paisa.models import ExpenseBankFilter, ExpenseGrouping, ExpenseSnapshot, ExpenseTimeframe, MonthlyTotal, SpendBreakdown


TIMEFRAME_OPTIONS: list[tuple[str, ExpenseTimeframe]] = [
    ("This Month", "this_month"),
    ("Last 3 Months", "last_3_months"),
    ("This Year", "this_year"),
    ("All Time", "all_time"),
]

GROUPING_OPTIONS: list[tuple[str, ExpenseGrouping]] = [
    ("Category", "category"),
    ("Bank", "bank"),
]

BANK_OPTIONS: list[tuple[str, ExpenseBankFilter]] = [
    ("All Banks", "all"),
    ("HDFC", "HDFC"),
    ("ICICI", "ICICI"),
]

TIMEFRAME_LABELS = {value: label for label, value in TIMEFRAME_OPTIONS}
BANK_LABELS = {value: label for label, value in BANK_OPTIONS}


def _format_month_label(value: str) -> str:
    year, month = value.split("-", maxsplit=1)
    month_names = {
        "01": "Jan",
        "02": "Feb",
        "03": "Mar",
        "04": "Apr",
        "05": "May",
        "06": "Jun",
        "07": "Jul",
        "08": "Aug",
        "09": "Sep",
        "10": "Oct",
        "11": "Nov",
        "12": "Dec",
    }
    return f"{month_names.get(month, month)} {year}"


def _format_money(amount: float) -> str:
    return f"Rs {amount:,.2f}"


class ExpensesView(VerticalScroll):
    def __init__(self, db_path: Path, **kwargs) -> None:
        super().__init__(classes="screen-view", **kwargs)
        self.db_path = db_path
        self._timeframe: ExpenseTimeframe = "this_month"
        self._bank: ExpenseBankFilter = "all"
        self._grouping: ExpenseGrouping = "category"

    def compose(self) -> ComposeResult:
        yield Static("Expenses", classes="section-title")
        yield Static("Choose filters below.", classes="placeholder-copy", id="expenses-filter-copy")
        with Vertical(id="expenses-filters"):
            yield Static("Timeframe", classes="expenses-filter-title")
            with Horizontal(classes="expenses-filter-row"):
                for label, value in TIMEFRAME_OPTIONS:
                    yield Button(
                        label,
                        id=f"expenses-timeframe-{value}",
                        classes="expenses-filter-button",
                        compact=True,
                    )
            yield Static("Bank", classes="expenses-filter-title")
            with Horizontal(classes="expenses-filter-row"):
                for label, value in BANK_OPTIONS:
                    yield Button(
                        label,
                        id=f"expenses-bank-{value}",
                        classes="expenses-filter-button",
                        compact=True,
                    )
            yield Static("Group By", classes="expenses-filter-title")
            with Horizontal(classes="expenses-filter-row"):
                for label, value in GROUPING_OPTIONS:
                    yield Button(
                        label,
                        id=f"expenses-grouping-{value}",
                        classes="expenses-filter-button",
                        compact=True,
                    )
        yield Static("", id="expenses-status", classes="placeholder-copy placeholder-box")
        yield Static("Spend Breakdown", classes="section-title")
        yield PlotextPlot(id="expenses-category-chart", classes="chart-card")
        yield Static("", id="expenses-empty", classes="placeholder-copy placeholder-box")
        yield Static("Investments by Month", classes="section-title")
        yield PlotextPlot(id="expenses-investments-chart", classes="chart-card")
        yield Static("", id="expenses-investments-empty", classes="placeholder-copy placeholder-box")

    def on_mount(self) -> None:
        self._sync_filter_buttons()
        self.load_expense_state()

    @work(exclusive=True)
    async def load_expense_state(self) -> None:
        snapshot = await get_expense_snapshot(
            self.db_path,
            timeframe=self._timeframe,
            bank=self._bank,
            grouping=self._grouping,
        )
        self._update_expense_state(snapshot)

    def refresh_view(self) -> None:
        self.load_expense_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""

        if button_id.startswith("expenses-timeframe-"):
            self._timeframe = button_id.removeprefix("expenses-timeframe-")
        elif button_id.startswith("expenses-bank-"):
            self._bank = button_id.removeprefix("expenses-bank-")
        elif button_id.startswith("expenses-grouping-"):
            self._grouping = button_id.removeprefix("expenses-grouping-")
        else:
            return

        self._sync_filter_buttons()
        self.load_expense_state()

    def _sync_filter_buttons(self) -> None:
        self._set_selected_button("expenses-timeframe", self._timeframe)
        self._set_selected_button("expenses-bank", self._bank)
        self._set_selected_button("expenses-grouping", self._grouping)

    def _set_selected_button(self, prefix: str, selected_value: str) -> None:
        for button in self.query(".expenses-filter-button"):
            button_id = button.id or ""
            if not button_id.startswith(f"{prefix}-"):
                continue
            value = button_id.removeprefix(f"{prefix}-")
            button.variant = "primary" if value == selected_value else "default"

    def _update_expense_state(self, snapshot: ExpenseSnapshot) -> None:
        status = self.query_one("#expenses-status", Static)
        chart = self.query_one("#expenses-category-chart", PlotextPlot)
        empty = self.query_one("#expenses-empty", Static)

        if snapshot.debit_transaction_count == 0:
            status.display = True
            status.update(
                f"{self._selected_timeframe_label()} • {self._selected_bank_label()}: no non-investment expense transactions in this filter."
            )
            chart.display = False
            empty.display = True
            empty.update("No expense transactions yet for the selected timeframe and bank.")
            self._render_investments(snapshot.monthly_investments)
            return

        status.display = True
        status.update(
            f"{self._selected_timeframe_label()} • {self._selected_bank_label()}: {snapshot.debit_transaction_count} debit transactions, {snapshot.category_count} expense categories, tracked spend: {_format_money(snapshot.total_spend)}."
        )
        empty.display = False
        self._render_breakdown(snapshot.breakdown)
        self._render_investments(snapshot.monthly_investments)

    def _render_breakdown(self, breakdown: list[SpendBreakdown]) -> None:
        chart = self.query_one("#expenses-category-chart", PlotextPlot)
        empty = self.query_one("#expenses-empty", Static)

        if not breakdown:
            chart.display = False
            empty.display = True
            empty.update("No grouped expense totals available for this filter yet.")
            return

        chart.display = True
        empty.display = False
        labels = [item.label for item in breakdown]
        values = [item.total_amount for item in breakdown]

        chart.plt.clear_figure()
        chart.plt.clear_data()
        chart.plt.title("Spend by Category" if self._grouping == "category" else "Spend by Bank")
        chart.plt.bar(labels, values)
        chart.plt.ylabel("Rs")
        chart.refresh()

    def _render_investments(self, monthly_investments: list[MonthlyTotal]) -> None:
        chart = self.query_one("#expenses-investments-chart", PlotextPlot)
        empty = self.query_one("#expenses-investments-empty", Static)

        if not monthly_investments:
            chart.display = False
            empty.display = True
            empty.update("No investment debits for the selected timeframe and bank.")
            return

        chart.display = True
        empty.display = False

        labels = [_format_month_label(item.month) for item in monthly_investments]
        values = [item.total_amount for item in monthly_investments]

        chart.plt.clear_figure()
        chart.plt.clear_data()
        chart.plt.title("Investments by Month")
        chart.plt.bar(labels, values)
        chart.plt.ylabel("Rs")
        chart.refresh()

    def _selected_timeframe_label(self) -> str:
        return TIMEFRAME_LABELS[self._timeframe]

    def _selected_bank_label(self) -> str:
        return BANK_LABELS[self._bank]
