from sqlalchemy import Engine, inspect

from transcriber.models import Base, Language, RecordingStatus


def test_models_expose_the_approved_languages_and_statuses() -> None:
    assert {language.value for language in Language} == {"en", "de", "tr"}
    assert RecordingStatus.COMPLETED.value == "completed"
    assert RecordingStatus.DELETING.value == "deleting"


def test_recordings_have_a_per_user_active_index() -> None:
    table = Base.metadata.tables["recordings"]
    indexes = {str(index.name): index for index in table.indexes}

    index = indexes["uq_recordings_one_active_per_user"]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["user_id"]
    assert index.dialect_options["postgresql"]["where"] is not None


def test_migration_created_all_tables(migrated_engine: Engine) -> None:
    table_names = set(inspect(migrated_engine).get_table_names())

    assert table_names == {
        "alembic_version",
        "auth_sessions",
        "login_attempts",
        "recordings",
        "transcription_chunks",
        "upload_parts",
        "upload_sessions",
        "users",
    }


def test_sessions_and_recordings_reference_users(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    session_foreign_keys = inspector.get_foreign_keys("auth_sessions")
    recording_foreign_keys = inspector.get_foreign_keys("recordings")

    assert any(key["referred_table"] == "users" for key in session_foreign_keys)
    assert any(key["referred_table"] == "users" for key in recording_foreign_keys)
