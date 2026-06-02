from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from paisa.models import TransactionInput, TransactionType
from paisa.parsers.merchant_cleaner import clean_merchant_name


DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y")
TRANSACTION_LINE_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})\s*(?P<body>.*?)\s+(?P<amount>\d[\d,]*\.\d{2})\s+(?P<balance>\d[\d,]*\.\d{2})$"
)
OPENING_BALANCE_RE = re.compile(r"^(?P<date>\d{2}-\d{2}-\d{4})\s+B/F\s+(?P<balance>\d[\d,]*\.\d{2})$")


def is_icici_statement(text: str) -> bool:
    return "ICICI" in text.upper()


def parse_icici_statement(text: str, tables: list[list[list[Any]]]) -> list[TransactionInput]:
    transactions = _parse_icici_tables(tables)
    if transactions:
        return transactions

    transactions = parse_icici_text(text)
    if not transactions:
        raise ValueError("No ICICI transactions found")
    return transactions


def parse_icici_tables(tables: list[list[list[Any]]]) -> list[TransactionInput]:
    transactions = _parse_icici_tables(tables)
    if not transactions:
        raise ValueError("No ICICI transactions found")
    return transactions


def parse_icici_text(text: str) -> list[TransactionInput]:
    transactions: list[TransactionInput] = []
    pending_narration: list[str] = []
    in_transaction_section = False
    previous_balance: float | None = None
    can_append_continuation = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Account Related Other Information"):
            break
        if line.startswith("Statement of Transactions in Savings Account"):
            in_transaction_section = False
            pending_narration.clear()
            can_append_continuation = False
            continue
        if line.upper() == "DATE MODE PARTICULARS DEPOSITS WITHDRAWALS BALANCE":
            in_transaction_section = True
            pending_narration.clear()
            can_append_continuation = False
            continue
        if not in_transaction_section:
            continue
        if line.startswith("Page ") or line.startswith("Total:"):
            pending_narration.clear()
            can_append_continuation = False
            continue

        opening_match = OPENING_BALANCE_RE.match(line)
        if opening_match:
            previous_balance = _parse_money(opening_match.group("balance"))
            pending_narration.clear()
            can_append_continuation = False
            continue

        transaction_match = TRANSACTION_LINE_RE.match(line)
        if transaction_match:
            transaction_date = _parse_date(transaction_match.group("date"))
            if transaction_date is None:
                pending_narration.append(line)
                can_append_continuation = False
                continue

            amount = _parse_money(transaction_match.group("amount"))
            balance = _parse_money(transaction_match.group("balance"))
            if amount is None or balance is None:
                pending_narration.append(line)
                can_append_continuation = False
                continue

            body = transaction_match.group("body").strip()
            narration_parts = [*pending_narration]
            if body and body.upper() != "B/F":
                narration_parts.append(body)
            narration = " ".join(narration_parts).strip()
            pending_narration.clear()

            transaction_type = _infer_transaction_type(previous_balance, amount, balance)
            previous_balance = balance
            can_append_continuation = not body and bool(transactions or narration)

            if not narration or transaction_type is None:
                continue

            transactions.append(
                TransactionInput(
                    bank="ICICI",
                    date=transaction_date,
                    narration=narration,
                    merchant_name=clean_merchant_name(narration),
                    amount=amount,
                    type=transaction_type,
                    running_balance=balance,
                )
            )
            continue

        if can_append_continuation and transactions and _looks_like_continuation(line):
            transactions[-1] = _append_narration(transactions[-1], line)
            can_append_continuation = False
            previous_balance = transactions[-1].amount if previous_balance is None else previous_balance
            continue

        pending_narration.append(line)
        can_append_continuation = False

    return transactions


def _parse_icici_tables(tables: list[list[list[Any]]]) -> list[TransactionInput]:
    transactions: list[TransactionInput] = []
    header: list[str] | None = None

    for table in tables:
        for raw_row in table:
            row = [_cell_text(cell) for cell in raw_row]
            if not any(row):
                continue
            if _looks_like_header(row):
                header = row
                continue
            if header is None:
                continue

            date_index = _find_column(header, "date")
            narration_index = _find_column(header, "remarks", "description", "particulars", "narration")
            debit_index = _find_column(header, "withdrawal", "debit")
            credit_index = _find_column(header, "deposit", "credit")
            balance_index = _find_column(header, "balance")

            transaction_date = _parse_date(row[date_index]) if date_index is not None else None
            if transaction_date is None:
                continue

            narration = row[narration_index] if narration_index is not None else _best_text_cell(row)
            debit = _parse_money(row[debit_index]) if debit_index is not None else None
            credit = _parse_money(row[credit_index]) if credit_index is not None else None
            running_balance = _parse_money(row[balance_index]) if balance_index is not None else None
            amount = debit or credit
            if amount is None:
                continue

            transaction_type: TransactionType = "DEBIT" if debit else "CREDIT"
            transactions.append(
                TransactionInput(
                    bank="ICICI",
                    date=transaction_date,
                    narration=narration,
                    merchant_name=clean_merchant_name(narration),
                    amount=amount,
                    type=transaction_type,
                    running_balance=running_balance,
                )
            )

    return transactions


def _looks_like_header(row: list[str]) -> bool:
    joined = " ".join(row).lower()
    return "date" in joined and any(
        word in joined for word in ["remarks", "description", "particulars", "narration"]
    )


def _find_column(header: list[str], *needles: str) -> int | None:
    for index, cell in enumerate(header):
        lower_cell = cell.lower()
        if any(needle in lower_cell for needle in needles):
            return index
    return None


def _cell_text(cell: Any) -> str:
    return str(cell or "").replace("\n", " ").strip()


def _parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def _parse_money(value: str) -> float | None:
    cleaned = value.replace(",", "").strip()
    cleaned = re.sub(r"\s*(cr|dr)\s*$", "", cleaned, flags=re.IGNORECASE)
    if not cleaned or cleaned in {"-", "0", "0.00"}:
        return None
    match = re.search(r"-?\d+(?:\.\d{1,2})?", cleaned)
    return abs(float(match.group(0))) if match else None


def _best_text_cell(row: list[str]) -> str:
    text_cells = [cell for cell in row if cell and not _parse_date(cell) and _parse_money(cell) is None]
    return max(text_cells, key=len, default="")


def _infer_transaction_type(
    previous_balance: float | None, amount: float, balance: float
) -> TransactionType | None:
    if previous_balance is None:
        return None
    if abs((previous_balance + amount) - balance) < 0.01:
        return "CREDIT"
    if abs((previous_balance - amount) - balance) < 0.01:
        return "DEBIT"
    return None


def _looks_like_continuation(line: str) -> bool:
    if _parse_date(line) is not None:
        return False
    if line.startswith("Page ") or line.startswith("Total:"):
        return False
    if line.upper() == "DATE MODE PARTICULARS DEPOSITS WITHDRAWALS BALANCE":
        return False
    if _looks_like_transaction_narration(line):
        return False
    return "/" not in line[:8] and len(line) > 3


def _looks_like_transaction_narration(line: str) -> bool:
    upper_line = line.upper()
    return upper_line.startswith(("UPI/", "NEFT", "IMPS", "RTGS", "ACH", "NWD", "POS"))


def _append_narration(transaction: TransactionInput, extra_text: str) -> TransactionInput:
    narration = f"{transaction.narration} {extra_text}".strip()
    return TransactionInput(
        bank=transaction.bank,
        date=transaction.date,
        narration=narration,
        merchant_name=clean_merchant_name(narration),
        amount=transaction.amount,
        type=transaction.type,
        running_balance=transaction.running_balance,
        category_id=transaction.category_id,
        id=transaction.id,
    )
