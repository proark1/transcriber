from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from transcriber.storage import ObjectMetadata, StorageError, StoredPart


class FakeObjectStorage:
    def __init__(self) -> None:
        self.uploads: dict[str, tuple[str, dict[int, StoredPart]]] = {}
        self.objects: dict[str, ObjectMetadata] = {}
        self.created_keys: list[str] = []
        self.presign_expirations: list[int] = []
        self.aborted_uploads: list[str] = []
        self.complete_calls = 0
        self.fail_list = False
        self.fail_complete = False
        self.fail_abort = False
        self.fail_delete = False

    @property
    def last_upload_id(self) -> str:
        return next(reversed(self.uploads))

    def create_multipart(self, object_key: str, content_type: str) -> str:
        del content_type
        upload_id = uuid4().hex
        self.uploads[upload_id] = (object_key, {})
        self.created_keys.append(object_key)
        return upload_id

    def presign_upload_part(
        self,
        object_key: str,
        provider_upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        if provider_upload_id not in self.uploads:
            raise StorageError("missing")
        self.presign_expirations.append(expires_seconds)
        nonce = uuid4().hex
        return f"https://bucket.test/{object_key}?part={part_number}&nonce={nonce}"

    def list_parts(self, object_key: str, provider_upload_id: str) -> list[StoredPart]:
        if self.fail_list:
            raise StorageError("list failed")
        upload = self.uploads.get(provider_upload_id)
        if upload is None or upload[0] != object_key:
            raise StorageError("missing")
        return sorted(upload[1].values(), key=lambda part: part.part_number)

    def complete_multipart(
        self,
        object_key: str,
        provider_upload_id: str,
        parts: list[StoredPart],
    ) -> None:
        self.complete_calls += 1
        if self.fail_complete:
            raise StorageError("complete failed")
        upload = self.uploads.pop(provider_upload_id, None)
        if upload is None or upload[0] != object_key:
            raise StorageError("missing")
        self.objects[object_key] = ObjectMetadata(
            size_bytes=sum(part.size_bytes for part in parts),
            content_type="application/octet-stream",
        )

    def abort_multipart(self, object_key: str, provider_upload_id: str) -> None:
        del object_key
        if self.fail_abort:
            raise StorageError("abort failed")
        self.uploads.pop(provider_upload_id, None)
        self.aborted_uploads.append(provider_upload_id)

    def head_object(self, object_key: str) -> ObjectMetadata | None:
        return self.objects.get(object_key)

    def download_file(self, object_key: str, destination: Path) -> None:
        metadata = self.objects.get(object_key)
        if metadata is None:
            raise StorageError("missing")
        destination.write_bytes(b"x" * metadata.size_bytes)

    def upload_file(self, object_key: str, source: Path, content_type: str) -> None:
        self.objects[object_key] = ObjectMetadata(source.stat().st_size, content_type)

    def delete_objects(self, object_keys: list[str]) -> set[str]:
        if self.fail_delete:
            raise StorageError("delete failed")
        for key in object_keys:
            self.objects.pop(key, None)
        return set()

    def presign_get(self, object_key: str, expires_seconds: int) -> str:
        if object_key not in self.objects:
            raise StorageError("missing")
        return f"https://bucket.test/{object_key}?expires={expires_seconds}"

    def put_part(self, provider_upload_id: str, part_number: int, size_bytes: int) -> None:
        upload = self.uploads[provider_upload_id]
        upload[1][part_number] = StoredPart(
            part_number=part_number,
            etag=f'"etag-{part_number}"',
            size_bytes=size_bytes,
        )
