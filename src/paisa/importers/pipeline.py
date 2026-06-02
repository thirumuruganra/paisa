from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from paisa.categorization import CategorizationResolver, CategorizationService
from paisa.db.migrations import initialize_database
from paisa.db.queries import insert_imported_statement, is_file_parsed
from paisa.importers.hashing import compute_file_hash
from paisa.importers.password_store import PasswordStore, PasswordStoreError
from paisa.models import ImportedFile, ImportFailure, ImportResult, ParsedStatement
from paisa.parsers.base import InvalidPasswordError, ParseError, parse_statement


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
    password_store = password_store or PasswordStore()
    session_passwords: list[str] = []
    categorizer = CategorizationService(db_path)

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
        except PasswordStoreError as exc:
            failures.append(ImportFailure(pdf_path.name, str(exc)))
        except ParseError as exc:
            failures.append(ImportFailure(pdf_path.name, str(exc)))
        except Exception:
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
    tried_passwords: set[str | None] = set()

    for password in [None, *session_passwords]:
        if password in tried_passwords:
            continue
        tried_passwords.add(password)
        try:
            parsed = await asyncio.to_thread(parse_statement, pdf_path, password, debug_pdf)
        except InvalidPasswordError:
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
            parsed = await asyncio.to_thread(parse_statement, pdf_path, password, debug_pdf)
        except InvalidPasswordError:
            continue
        else:
            if password:
                _store_password_if_possible(password_store, parsed.bank, password)
            return parsed

    if password_provider is not None:
        prompted_password = password_provider(pdf_path)
        if prompted_password and prompted_password not in tried_passwords:
            try:
                parsed = await asyncio.to_thread(parse_statement, pdf_path, prompted_password, debug_pdf)
            except InvalidPasswordError as exc:
                raise InvalidPasswordError("PDF password is required or invalid") from exc
            session_passwords.append(prompted_password)
            _store_password_if_possible(password_store, parsed.bank, prompted_password)
            return parsed

    raise InvalidPasswordError("PDF password is required or invalid")


def _get_stored_password_if_possible(password_store: PasswordStore, bank: str) -> str | None:
    try:
        return password_store.get_password(bank)
    except PasswordStoreError:
        return None


def _store_password_if_possible(password_store: PasswordStore, bank: str, password: str) -> None:
    try:
        password_store.set_password(bank, password)
    except PasswordStoreError:
        return
