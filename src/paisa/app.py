from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from paisa.config import default_db_path, default_statements_dir, load_settings

if TYPE_CHECKING:
    from paisa.db.migrations import initialize_database as initialize_database_type
    from paisa.importers.pipeline import import_statements as import_statements_type
    from paisa.tui.app import PaisaTuiApp as PaisaTuiAppType


initialize_database = None
import_statements = None
PaisaTuiApp = None


def main() -> None:
    load_dotenv(Path.cwd() / ".env")

    parser = argparse.ArgumentParser(prog="paisa")
    parser.add_argument("--db-path", type=Path, default=default_db_path())
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init-db", help="Initialize the local SQLite database")

    import_parser = subparsers.add_parser("import", help="Import statement PDFs")
    import_parser.add_argument("--statements-dir", type=Path, default=default_statements_dir())
    import_parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Do not prompt for PDF passwords",
    )
    import_parser.add_argument(
        "--debug-pdf",
        action="store_true",
        help="Print extracted PDF text and tables while importing",
    )

    args = parser.parse_args()
    settings = load_settings(db_path=args.db_path, statements_dir=getattr(args, "statements_dir", None))

    if args.command is None:
        asyncio.run(_initialize_database()(settings.db_path))
        _paisa_tui_app()(settings=settings).run()
        return

    if args.command == "init-db":
        asyncio.run(_initialize_database()(settings.db_path))
        print(f"Initialized database: {settings.db_path}")
        return

    if args.command == "import":
        password_provider = None if args.no_prompt else _prompt_for_password
        result = asyncio.run(
            _import_statements()(
                db_path=settings.db_path,
                statements_dir=settings.statements_dir,
                password_provider=password_provider,
                debug_pdf=args.debug_pdf,
            )
        )
        print(f"Imported files: {result.imported_files}")
        print(f"Imported transactions: {result.imported_transactions}")
        print(f"Skipped duplicates: {result.skipped_duplicates}")
        if result.failures:
            print("Failures:")
            for failure in result.failures:
                print(f"- {failure.filename}: {failure.reason}")


def _prompt_for_password(path: Path) -> str | None:
    password = getpass(f"PDF password for {path.name}: ")
    return password or None


def _initialize_database():
    global initialize_database
    if initialize_database is None:
        from paisa.db.migrations import initialize_database as initialize_database_fn

        initialize_database = initialize_database_fn
    return initialize_database


def _import_statements():
    global import_statements
    if import_statements is None:
        from paisa.importers.pipeline import import_statements as import_statements_fn

        import_statements = import_statements_fn
    return import_statements


def _paisa_tui_app():
    global PaisaTuiApp
    if PaisaTuiApp is None:
        from paisa.tui.app import PaisaTuiApp as paisa_tui_app_class

        PaisaTuiApp = paisa_tui_app_class
    return PaisaTuiApp
