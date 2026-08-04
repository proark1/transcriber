"""Best-effort cleanup of temporary chunk objects after durable assembly."""

from __future__ import annotations

from transcriber.storage import ObjectStorage, StorageError


def remove_working_chunks(storage: ObjectStorage, object_keys: list[str]) -> bool:
    try:
        failed = storage.delete_objects(object_keys)
    except StorageError:
        return False
    return not failed
