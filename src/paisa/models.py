from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4


Bank = Literal["HDFC", "ICICI"]
TransactionType = Literal["DEBIT", "CREDIT"]
ExpenseTimeframe = Literal["this_month", "last_3_months", "this_year", "all_time"]
ExpenseGrouping = Literal["category", "bank"]
ExpenseBankFilter = Bank | Literal["all"]


@dataclass(frozen=True)
class TransactionInput:
    bank: Bank
    date: date
    narration: str
    merchant_name: str
    amount: float
    type: TransactionType
    running_balance: float | None = None
    category_id: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ParsedStatement:
    bank: Bank
    transactions: list[TransactionInput]


@dataclass(frozen=True)
class ImportedFile:
    file_hash: str
    filename: str
    bank: Bank
    import_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class LedgerTransaction:
    id: str
    bank: Bank
    date: date
    narration: str
    merchant_name: str
    amount: float
    type: TransactionType
    category_name: str


@dataclass(frozen=True)
class DashboardSnapshot:
    current_balance: float
    income_this_month: float
    expenses_this_month: float
    recent_transactions: list[LedgerTransaction]
    rolling_balance: list["DailyBalancePoint"] = field(default_factory=list)


@dataclass(frozen=True)
class ExpenseSnapshot:
    debit_transaction_count: int
    category_count: int
    total_spend: float
    breakdown: list["SpendBreakdown"] = field(default_factory=list)
    monthly_investments: list["MonthlyTotal"] = field(default_factory=list)


@dataclass(frozen=True)
class DailyBalancePoint:
    day: date
    balance: float


@dataclass(frozen=True)
class CategorySpend:
    category_id: int
    category_name: str
    total_amount: float


@dataclass(frozen=True)
class SpendBreakdown:
    label: str
    total_amount: float


@dataclass(frozen=True)
class MonthlyTotal:
    month: str
    total_amount: float


@dataclass(frozen=True)
class CategorySummary:
    category_id: int
    category_name: str
    transaction_count: int
    rule_count: int
    example_count: int


@dataclass(frozen=True)
class CategoryExample:
    merchant_key: str
    sample_text: str
    category_id: int
    category_name: str


@dataclass(frozen=True)
class CategorizationCandidate:
    category_id: int
    category_name: str
    score: float
    source: str


@dataclass(frozen=True)
class CategorizationPrompt:
    transaction_id: str
    merchant_name: str
    narration: str
    confidence: float
    suggested_category_id: int | None
    suggested_category_name: str | None
    category_options: list[tuple[int, str]]
    candidate_categories: list[CategorizationCandidate] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class CategorizationPendingReview:
    thread_id: str
    prompt: CategorizationPrompt


@dataclass(frozen=True)
class CategorizationResolution:
    category_id: int | None = None
    new_category_name: str | None = None
    skipped: bool = False


@dataclass(frozen=True)
class CategorizationDecision:
    category_id: int
    category_name: str
    confidence: float
    source: str
    prompt: CategorizationPrompt | None = None


@dataclass(frozen=True)
class ImportFailure:
    filename: str
    reason: str


@dataclass(frozen=True)
class ImportResult:
    imported_files: int = 0
    imported_transactions: int = 0
    skipped_duplicates: int = 0
    failures: list[ImportFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures
