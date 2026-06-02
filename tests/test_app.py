from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from paisa import app as app_module


def test_main_loads_dotenv_before_bootstrap(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    fake_db_path = tmp_path / "paisa.db"
    fake_statements_dir = tmp_path / "statements"

    class FakeTuiApp:
        def __init__(self, *, settings) -> None:
            assert settings.db_path == fake_db_path
            assert settings.statements_dir == fake_statements_dir

        def run(self) -> None:
            calls.append("run")

    async def fake_initialize_database(db_path: Path) -> None:
        assert db_path == fake_db_path
        calls.append("init-db")

    def fake_load_dotenv(path: Path) -> None:
        assert path == Path.cwd() / ".env"
        calls.append("load-dotenv")

    monkeypatch.setattr(app_module, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(app_module, "initialize_database", fake_initialize_database)
    monkeypatch.setattr(app_module, "PaisaTuiApp", FakeTuiApp)
    monkeypatch.setattr(
        app_module,
        "load_settings",
        lambda db_path=None, statements_dir=None: SimpleNamespace(
            db_path=fake_db_path,
            statements_dir=fake_statements_dir,
        ),
    )
    monkeypatch.setattr(app_module.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {"db_path": fake_db_path, "command": None})())

    app_module.main()

    assert calls == ["load-dotenv", "init-db", "run"]