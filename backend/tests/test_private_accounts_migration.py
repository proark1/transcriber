from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, inspect


def migration_module() -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0004_private_accounts.py"
    spec = spec_from_file_location("private_accounts_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the private-account migration.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_database_upgrade_creates_private_account_schema(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)

    assert "users" in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns("auth_sessions")} >= {"user_id"}
    assert {column["name"] for column in inspector.get_columns("recordings")} >= {"user_id"}


def test_upgrade_guard_stops_before_schema_mutation_when_recordings_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = migration_module()
    results = iter([1, 0])

    class GuardConnection:
        def scalar(self, _statement: object) -> int:
            return next(results)

    monkeypatch.setattr(migration.op, "get_bind", lambda: GuardConnection())

    def unexpected_mutation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Migration mutated schema after the empty-state guard failed.")

    monkeypatch.setattr(migration.op, "create_table", unexpected_mutation)

    with pytest.raises(RuntimeError, match="requires empty recordings"):
        migration.upgrade()


def test_private_account_migration_is_explicitly_forward_only() -> None:
    migration = migration_module()

    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
