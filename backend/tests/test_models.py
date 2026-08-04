from sqlalchemy import Engine, inspect

from transcriber.models import Base, Language, RecordingStatus


def test_models_expose_the_approved_languages_and_statuses() -> None:
    assert {language.value for language in Language} == {"en", "de", "tr"}
    assert RecordingStatus.COMPLETED.value == "completed"
    assert RecordingStatus.DELETING.value == "deleting"


def test_recordings_have_the_single_active_index() -> None:
    table = Base.metadata.tables["recordings"]
    indexes = {str(index.name): index for index in table.indexes}

    assert indexes["uq_recordings_one_active"].unique is True
    assert indexes["uq_recordings_one_active"].dialect_options["postgresql"]["where"] is not None


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
    }
