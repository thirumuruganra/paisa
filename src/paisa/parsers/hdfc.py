from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from paisa.models import TransactionInput, TransactionType
from paisa.parsers.merchant_cleaner import clean_merchant_name


DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y")


def is_hdfc_statement(text: str) -> bool:
    upper_text = text.upper()
    return "HDFC" in upper_text and "ICICI" not in upper_text


def parse_hdfc_tables(tables: list[list[list[Any]]]) -> list[TransactionInput]:
    transactions: list[TransactionInput] = []
    header: list[str] | None = None
    current: TransactionInput | None = None

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
            narration_index = _find_column(header, "narration", "description", "particulars")
            debit_index = _find_column(header, "withdrawal", "debit")
            credit_index = _find_column(header, "deposit", "credit")
            balance_index = _find_column(header, "closing balance", "balance")

            transaction_date = _parse_date(row[date_index]) if date_index is not None else None
            if transaction_date is None:
                if current is not None:
                    extra_text = _continuation_text(row, debit_index, credit_index)
                    if extra_text:
                        current = _append_narration(current, extra_text)
                        transactions[-1] = current
                continue

            narration = row[narration_index] if narration_index is not None else _best_text_cell(row)
            debit = _parse_money(row[debit_index]) if debit_index is not None else None
            credit = _parse_money(row[credit_index]) if credit_index is not None else None
            running_balance = _parse_money(row[balance_index]) if balance_index is not None else None

            amount = debit or credit
            if amount is None:
                continue

            transaction_type: TransactionType = "DEBIT" if debit else "CREDIT"
            current = TransactionInput(
                bank="HDFC",
                date=transaction_date,
                narration=narration,
                merchant_name=clean_merchant_name(narration),
                amount=amount,
                type=transaction_type,
                running_balance=running_balance,
            )
            transactions.append(current)

    if not transactions:
        raise ValueError("No HDFC transactions found")
    return transactions


def _looks_like_header(row: list[str]) -> bool:
    joined = " ".join(row).lower()
    return "date" in joined and any(word in joined for word in ["narration", "description"])


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


def _continuation_text(row: list[str], debit_index: int | None, credit_index: int | None) -> str:
    ignored = {index for index in [debit_index, credit_index] if index is not None}
    parts = [
        cell
        for index, cell in enumerate(row)
        if index not in ignored and cell and _parse_date(cell) is None and _parse_money(cell) is None
    ]
    return " ".join(parts).strip()


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
