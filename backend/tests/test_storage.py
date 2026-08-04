from __future__ import annotations

from pathlib import Path
from typing import Any

from transcriber.config import AppSettings
from transcriber.storage import BotoObjectStorage, StoredPart


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_count = 0

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("create", kwargs))
        return {"UploadId": "provider-id"}

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.calls.append((operation, kwargs))
        return "https://storage.example/signed"

    def list_parts(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        self.list_count += 1
        if self.list_count == 1:
            return {
                "Parts": [{"PartNumber": 2, "ETag": '"two"', "Size": 5}],
                "IsTruncated": True,
                "NextPartNumberMarker": 2,
            }
        return {
            "Parts": [{"PartNumber": 1, "ETag": '"one"', "Size": 10}],
            "IsTruncated": False,
        }

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("complete", kwargs))
        return {}

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("abort", kwargs))
        return {}

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("head", kwargs))
        return {"ContentLength": 15, "ContentType": "audio/mp4"}

    def download_file(self, *args: object) -> None:
        self.calls.append(("download", {"args": args}))

    def upload_file(self, *args: object, **kwargs: Any) -> None:
        self.calls.append(("upload", {"args": args, **kwargs}))

    def delete_objects(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("delete", kwargs))
        return {"Errors": [{"Key": "failed"}]}


def test_boto_storage_uses_private_multipart_contract(app_settings: AppSettings) -> None:
    client = FakeS3Client()
    storage = BotoObjectStorage(app_settings, client=client)

    upload_id = storage.create_multipart("recordings/id/original/key", "audio/mp4")
    url = storage.presign_upload_part(
        "recordings/id/original/key", upload_id, 1, expires_seconds=900
    )
    parts = storage.list_parts("recordings/id/original/key", upload_id)
    storage.complete_multipart("recordings/id/original/key", upload_id, parts)

    assert upload_id == "provider-id"
    assert url == "https://storage.example/signed"
    assert [part.part_number for part in parts] == [1, 2]
    complete = next(call for call in client.calls if call[0] == "complete")
    assert complete[1]["MultipartUpload"] == {
        "Parts": [
            {"ETag": '"one"', "PartNumber": 1},
            {"ETag": '"two"', "PartNumber": 2},
        ]
    }


def test_boto_storage_supports_worker_and_cleanup_operations(
    app_settings: AppSettings, tmp_path: Path
) -> None:
    client = FakeS3Client()
    storage = BotoObjectStorage(app_settings, client=client)
    source = tmp_path / "source.m4a"
    source.write_bytes(b"audio")

    assert storage.head_object("object") is not None
    storage.download_file("object", tmp_path / "download.m4a")
    storage.upload_file("playback", source, "audio/mp4")
    failed = storage.delete_objects(["one", "failed"])
    get_url = storage.presign_get("playback", 300)

    assert failed == {"failed"}
    assert get_url == "https://storage.example/signed"
    assert any(call[0] == "upload" for call in client.calls)


def test_complete_sorts_parts_before_sending(app_settings: AppSettings) -> None:
    client = FakeS3Client()
    storage = BotoObjectStorage(app_settings, client=client)

    storage.complete_multipart(
        "key",
        "upload",
        [StoredPart(2, "two", 1), StoredPart(1, "one", 1)],
    )

    complete = next(call for call in client.calls if call[0] == "complete")
    assert complete[1]["MultipartUpload"] == {
        "Parts": [
            {"ETag": "one", "PartNumber": 1},
            {"ETag": "two", "PartNumber": 2},
        ]
    }
