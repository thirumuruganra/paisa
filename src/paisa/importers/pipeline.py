from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from paisa.db.migrations import initialize_database
from paisa.db.queries import insert_imported_statement, is_file_parsed
from paisa.importers.hashing import compute_file_hash
from paisa.models import ImportedFile, ImportFailure, ImportResult, ParsedStatement

if TYPE_CHECKING:
    from paisa.categorization import CategorizationResolver, CategorizationService
    from paisa.importers.password_store import PasswordStore


CategorizationService = None
PasswordStore = None
PasswordStoreError = None
parse_statement = None
InvalidPasswordError = None
ParseError = None


PasswordProvider = Callable[[Path], str | None]


async def import_statements(
    db_path: Path,
    statements_dir: Path,
    password_store: PasswordStore | None = None,
    password_provider: PasswordProvider | None = None,
    categorization_resolver: CategorizationResolver | None = None,
    debug_pdf: bool = False,
) -> ImportResult:
    await initialize_database(db_path)
    statements_dir.mkdir(parents=True, exist_ok=True)

    imported_files = 0
    imported_transactions = 0
    skipped_duplicates = 0
    failures: list[ImportFailure] = []
    password_store = password_store or _password_store_class()()
    session_passwords: list[str] = []
    categorizer = None

    for pdf_path in sorted(statements_dir.glob("*.pdf")):
        file_hash = await asyncio.to_thread(compute_file_hash, pdf_path)
        if await is_file_parsed(db_path, file_hash):
            skipped_duplicates += 1
            continue

        try:
            parsed = await _parse_with_available_passwords(
                pdf_path,
                password_store=password_store,
                session_passwords=session_passwords,
                password_provider=password_provider,
                debug_pdf=debug_pdf,
            )
            if categorizer is None:
                categorizer = _categorization_service_class()(db_path)
            categorized_transactions = await categorizer.categorize_transactions(
                parsed.transactions,
                resolver=categorization_resolver,
            )
            imported_transactions += await insert_imported_statement(
                db_path,
                ImportedFile(file_hash=file_hash, filename=pdf_path.name, bank=parsed.bank),
                categorized_transactions,
            )
            imported_files += 1
        except Exception as exc:
            if _is_parse_error(exc):
                failures.append(ImportFailure(pdf_path.name, str(exc)))
                continue
            failures.append(ImportFailure(pdf_path.name, "Import failed"))

    return ImportResult(
        imported_files=imported_files,
        imported_transactions=imported_transactions,
        skipped_duplicates=skipped_duplicates,
        failures=failures,
    )


async def _parse_with_available_passwords(
    pdf_path: Path,
    password_store: PasswordStore,
    session_passwords: list[str],
    password_provider: PasswordProvider | None,
    debug_pdf: bool,
) -> ParsedStatement:
    parse_statement_fn = _parse_statement_function()
    invalid_password_error = _invalid_password_error_class()
    tried_passwords: set[str | None] = set()

    for password in [None, *session_passwords]:
        if password in tried_passwords:
            continue
        tried_passwords.add(password)
        try:
            parsed = await asyncio.to_thread(parse_statement_fn, pdf_path, password, debug_pdf)
        except invalid_password_error:
            continue
        else:
            if password:
                _store_password_if_possible(password_store, parsed.bank, password)
            return parsed

    stored_passwords = [
        _get_stored_password_if_possible(password_store, "HDFC"),
        _get_stored_password_if_possible(password_store, "ICICI"),
    ]
    for password in stored_passwords:
        if password in tried_passwords:
            continue
        tried_passwords.add(password)
        try:
            parsed = await asyncio.to_thread(parse_statement_fn, pdf_path, password, debug_pdf)
        except invalid_password_error:
            continue
        else:
            if password:
                _store_password_if_possible(password_store, parsed.bank, password)
            return parsed

    if password_provider is not None:
        prompted_password = password_provider(pdf_path)
        if prompted_password and prompted_password not in tried_passwords:
            try:
                parsed = await asyncio.to_thread(parse_statement_fn, pdf_path, prompted_password, debug_pdf)
            except invalid_password_error as exc:
                raise invalid_password_error("PDF password is required or invalid") from exc
            session_passwords.append(prompted_password)
            _store_password_if_possible(password_store, parsed.bank, prompted_password)
            return parsed

    raise invalid_password_error("PDF password is required or invalid")


def _get_stored_password_if_possible(password_store: PasswordStore, bank: str) -> str | None:
    password_store_error = _password_store_error_class()
    try:
        return password_store.get_password(bank)
    except password_store_error:
        return None


def _store_password_if_possible(password_store: PasswordStore, bank: str, password: str) -> None:
    password_store_error = _password_store_error_class()
    try:
        password_store.set_password(bank, password)
    except password_store_error:
        return


def _categorization_service_class():
    global CategorizationService
    if CategorizationService is None:
        from paisa.categorization import CategorizationService as categorization_service_class

        CategorizationService = categorization_service_class
    return CategorizationService


def _password_store_class():
    global PasswordStore, PasswordStoreError
    if PasswordStore is None or PasswordStoreError is None:
        from paisa.importers.password_store import PasswordStore as password_store_class
        from paisa.importers.password_store import PasswordStoreError as password_store_error_class

        PasswordStore = password_store_class
        PasswordStoreError = password_store_error_class
    return PasswordStore


def _password_store_error_class():
    _password_store_class()
    return PasswordStoreError


def _parse_statement_function():
    global parse_statement, InvalidPasswordError, ParseError
    if parse_statement is None or InvalidPasswordError is None or ParseError is None:
        from paisa.parsers.base import InvalidPasswordError as invalid_password_error_class
        from paisa.parsers.base import ParseError as parse_error_class

        if parse_statement is None:
            from paisa.parsers.base import parse_statement as parse_statement_fn

            parse_statement = parse_statement_fn
        InvalidPasswordError = invalid_password_error_class
        ParseError = parse_error_class
    return parse_statement


def _invalid_password_error_class():
    _parse_statement_function()
    return InvalidPasswordError


def _parse_error_class():
    _parse_statement_function()
    return ParseError


def _is_parse_error(exc: Exception) -> bool:
    parse_error = ParseError
    return parse_error is not None and isinstance(exc, parse_error)
