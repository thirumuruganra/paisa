from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from paisa.models import ParsedStatement
from paisa.parsers.hdfc import is_hdfc_statement, parse_hdfc_tables
from paisa.parsers.icici import is_icici_statement, parse_icici_statement


class ParseError(Exception):
    """Base error for statement parsing failures."""


class InvalidPasswordError(ParseError):
    """Raised when a PDF cannot be opened because it needs a password."""


class UnsupportedBankError(ParseError):
    """Raised when the statement is not recognized as HDFC or ICICI."""


def parse_statement(path: Path, password: str | None = None, debug_pdf: bool = False) -> ParsedStatement:
    text, tables = extract_pdf_content(path, password=password)
    if debug_pdf:
        _print_debug_pdf_output(path, text, tables)
    if is_hdfc_statement(text):
        try:
            return ParsedStatement(bank="HDFC", transactions=parse_hdfc_tables(tables))
        except ValueError as exc:
            raise ParseError(str(exc)) from exc
    if is_icici_statement(text):
        try:
            return ParsedStatement(bank="ICICI", transactions=parse_icici_statement(text, tables))
        except ValueError as exc:
            raise ParseError(str(exc)) from exc
    raise UnsupportedBankError("Only HDFC and ICICI statements are supported")


def extract_pdf_content(path: Path, password: str | None = None) -> tuple[str, list[list[list[Any]]]]:
    try:
        with pdfplumber.open(path, password=password) as pdf:
            text_parts: list[str] = []
            tables: list[list[list[Any]]] = []
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
                tables.extend(page.extract_tables() or [])
    except PdfminerException as exc:
        if exc.args and isinstance(exc.args[0], PDFPasswordIncorrect):
            raise InvalidPasswordError("PDF password is required or invalid") from exc
        raise ParseError("Could not read PDF statement") from exc
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "decrypt" in message or "encrypted" in message:
            raise InvalidPasswordError("PDF password is required or invalid") from exc
        raise ParseError("Could not read PDF statement") from exc

    return "\n".join(text_parts), tables


def _print_debug_pdf_output(path: Path, text: str, tables: list[list[list[Any]]]) -> None:
    print(f"=== PDF DEBUG: {path.name} ===", file=sys.stderr)
    print("--- Extracted Text ---", file=sys.stderr)
    print(text or "<empty>", file=sys.stderr)
    print("--- Extracted Tables ---", file=sys.stderr)
    if not tables:
        print("<no tables>", file=sys.stderr)
    for table_index, table in enumerate(tables, start=1):
        print(f"[Table {table_index}]", file=sys.stderr)
        for row in table:
            print(row, file=sys.stderr)
    print("=== END PDF DEBUG ===", file=sys.stderr)
