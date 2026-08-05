from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from transcriber.models import Language, Recording, RecordingStatus, User
from transcriber.repositories import (
    ActiveRecordingExists,
    RecordingRepository,
    RecordingTransitionConflict,
)


def create_recording(
    repository: RecordingRepository,
    *,
    user: User,
    suffix: str = "one",
) -> Recording:
    return repository.create_uploading_recording(
        user_id=user.id,
        display_filename=f"recording-{suffix}.m4a",
        reported_content_type="audio/mp4",
        expected_bytes=1_024,
        language=Language.ENGLISH,
        original_object_key=f"recordings/{uuid4()}/original",
    )


def test_repository_prevents_a_second_active_recording(
    database_session: Session, test_user: User
) -> None:
    repository = RecordingRepository(database_session)
    create_recording(repository, user=test_user)
    database_session.commit()

    with pytest.raises(ActiveRecordingExists):
        create_recording(repository, user=test_user, suffix="two")


def test_different_users_may_each_have_an_active_recording(
    database_session: Session,
    test_user: User,
    user_factory: Callable[..., User],
) -> None:
    other_user = user_factory(username="other-user")
    repository = RecordingRepository(database_session)

    first = create_recording(repository, user=test_user)
    database_session.commit()
    second = create_recording(repository, user=other_user, suffix="two")

    assert first.user_id != second.user_id


def test_repository_transitions_only_from_an_expected_state(
    database_session: Session,
    test_user: User,
) -> None:
    repository = RecordingRepository(database_session)
    recording = create_recording(repository, user=test_user)
    database_session.commit()

    transitioned = repository.transition(
        recording.id,
        expected=[RecordingStatus.UPLOADING],
        target=RecordingStatus.QUEUED,
    )
    database_session.commit()

    assert transitioned.status is RecordingStatus.QUEUED
    with pytest.raises(RecordingTransitionConflict):
        repository.transition(
            recording.id,
            expected=[RecordingStatus.UPLOADING],
            target=RecordingStatus.VALIDATING,
        )
