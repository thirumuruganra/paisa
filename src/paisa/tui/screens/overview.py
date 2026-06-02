from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual_plotext import PlotextPlot
from textual.widgets import DataTable, Static

from paisa.db.queries import get_dashboard_snapshot
from paisa.models import DailyBalancePoint, DashboardSnapshot
from paisa.tui.widgets import SummaryCard


def _format_money(amount: float) -> str:
    return f"Rs {amount:,.2f}"


class OverviewView(VerticalScroll):
    def __init__(self, db_path: Path, logo_text: str, **kwargs) -> None:
        super().__init__(classes="screen-view", **kwargs)
        self.db_path = db_path
        self.logo_text = logo_text

    def compose(self) -> ComposeResult:
        yield Static(self.logo_text, id="overview-logo", markup=False)
        with Horizontal(id="overview-summary"):
            yield SummaryCard("Current Balance", id="summary-balance")
            yield SummaryCard("Income This Month", id="summary-income")
            yield SummaryCard("Expenses This Month", id="summary-expenses")
        yield Static("Rolling 30-Day Balance", classes="section-title")
        yield PlotextPlot(id="overview-balance-chart", classes="chart-card")
        yield Static(
            "Rolling balance chart appears after statement imports add transaction history.",
            id="overview-chart-empty",
            classes="placeholder-copy placeholder-box",
        )
        yield Static("Recent Transactions", classes="section-title")
        yield DataTable(id="overview-recent")
        yield Static(
            "No transactions imported yet. Import PDFs from statements/ to unlock balances, recent activity, and charts.",
            id="overview-empty",
            classes="placeholder-copy placeholder-box",
        )

    def on_mount(self) -> None:
        table = self.query_one("#overview-recent", DataTable)
        table.cursor_type = "row"
        table.add_columns("Date", "Merchant", "Type", "Amount")
        self.load_dashboard()

    @work(exclusive=True)
    async def load_dashboard(self) -> None:
        snapshot = await get_dashboard_snapshot(self.db_path)
        self._update_dashboard(snapshot)

    def refresh_view(self) -> None:
        self.load_dashboard()

    def _update_dashboard(self, snapshot: DashboardSnapshot) -> None:
        self.query_one("#summary-balance", SummaryCard).set_value(_format_money(snapshot.current_balance))
        self.query_one("#summary-income", SummaryCard).set_value(_format_money(snapshot.income_this_month))
        self.query_one("#summary-expenses", SummaryCard).set_value(_format_money(snapshot.expenses_this_month))

        table = self.query_one("#overview-recent", DataTable)
        table.clear(columns=False)
        for transaction in snapshot.recent_transactions:
            table.add_row(
                transaction.date.isoformat(),
                transaction.merchant_name,
                transaction.type,
                _format_money(transaction.amount),
            )

        empty = self.query_one("#overview-empty", Static)
        empty.display = not snapshot.recent_transactions
        table.display = bool(snapshot.recent_transactions)
        self._render_balance_chart(snapshot.rolling_balance)

    def _render_balance_chart(self, points: list[DailyBalancePoint]) -> None:
        chart = self.query_one("#overview-balance-chart", PlotextPlot)
        empty = self.query_one("#overview-chart-empty", Static)

        if not points:
            chart.display = False
            empty.display = True
            return

        chart.display = True
        empty.display = False

        positions = list(range(len(points)))
        labels = [point.day.strftime("%d %b") for point in points]
        values = [point.balance for point in points]
        tick_step = max(1, len(points) // 5)
        tick_positions = positions[::tick_step]
        tick_labels = labels[::tick_step]
        if tick_positions[-1] != positions[-1]:
            tick_positions.append(positions[-1])
            tick_labels.append(labels[-1])

        chart.plt.clear_figure()
        chart.plt.clear_data()
        chart.plt.title("Balance")
        chart.plt.plot(positions, values)
        chart.plt.xticks(tick_positions, tick_labels)
        chart.plt.ylabel("Rs")
        chart.refresh()
