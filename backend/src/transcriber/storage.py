"""Private S3-compatible object storage used by Railway and local MinIO."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from transcriber.config import AppSettings


class StorageError(RuntimeError):
    """An object operation failed without exposing the provider response."""


@dataclass(frozen=True)
class StoredPart:
    part_number: int
    etag: str
    size_bytes: int


@dataclass(frozen=True)
class ObjectMetadata:
    size_bytes: int
    content_type: str | None = None


class ObjectStorage(Protocol):
    def create_multipart(self, object_key: str, content_type: str) -> str: ...

    def presign_upload_part(
        self,
        object_key: str,
        provider_upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str: ...

    def list_parts(self, object_key: str, provider_upload_id: str) -> list[StoredPart]: ...

    def complete_multipart(
        self,
        object_key: str,
        provider_upload_id: str,
        parts: list[StoredPart],
    ) -> None: ...

    def abort_multipart(self, object_key: str, provider_upload_id: str) -> None: ...

    def head_object(self, object_key: str) -> ObjectMetadata | None: ...

    def download_file(self, object_key: str, destination: Path) -> None: ...

    def upload_file(self, object_key: str, source: Path, content_type: str) -> None: ...

    def delete_objects(self, object_keys: list[str]) -> set[str]: ...

    def presign_get(self, object_key: str, expires_seconds: int) -> str: ...


class BotoObjectStorage:
    """Small, provider-neutral wrapper around the S3 multipart API."""

    def __init__(self, settings: AppSettings, *, client: Any | None = None) -> None:
        self._bucket = settings.bucket_name
        self._client: Any = client or boto3.client(
            "s3",
            endpoint_url=settings.bucket_endpoint,
            region_name=settings.bucket_region,
            aws_access_key_id=settings.bucket_access_key_id,
            aws_secret_access_key=settings.bucket_secret_access_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": settings.bucket_url_style},
                retries={"max_attempts": 4, "mode": "standard"},
            ),
        )

    def create_multipart(self, object_key: str, content_type: str) -> str:
        try:
            response = self._client.create_multipart_upload(
                Bucket=self._bucket,
                Key=object_key,
                ContentType=content_type,
            )
            return cast(str, response["UploadId"])
        except (BotoCoreError, ClientError, KeyError) as error:
            raise StorageError("Could not create multipart upload.") from error

    def presign_upload_part(
        self,
        object_key: str,
        provider_upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        try:
            return cast(
                str,
                self._client.generate_presigned_url(
                    "upload_part",
                    Params={
                        "Bucket": self._bucket,
                        "Key": object_key,
                        "UploadId": provider_upload_id,
                        "PartNumber": part_number,
                    },
                    ExpiresIn=expires_seconds,
                    HttpMethod="PUT",
                ),
            )
        except (BotoCoreError, ClientError) as error:
            raise StorageError("Could not authorize upload part.") from error

    def list_parts(self, object_key: str, provider_upload_id: str) -> list[StoredPart]:
        parts: list[StoredPart] = []
        marker = 0
        try:
            while True:
                response = self._client.list_parts(
                    Bucket=self._bucket,
                    Key=object_key,
                    UploadId=provider_upload_id,
                    PartNumberMarker=marker,
                )
                for part in response.get("Parts", []):
                    parts.append(
                        StoredPart(
                            part_number=int(part["PartNumber"]),
                            etag=str(part["ETag"]),
                            size_bytes=int(part["Size"]),
                        )
                    )
                if not response.get("IsTruncated", False):
                    break
                marker = int(response["NextPartNumberMarker"])
        except (BotoCoreError, ClientError, KeyError, TypeError, ValueError) as error:
            raise StorageError("Could not inspect multipart upload.") from error
        return sorted(parts, key=lambda part: part.part_number)

    def complete_multipart(
        self,
        object_key: str,
        provider_upload_id: str,
        parts: list[StoredPart],
    ) -> None:
        try:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=object_key,
                UploadId=provider_upload_id,
                MultipartUpload={
                    "Parts": [
                        {"ETag": part.etag, "PartNumber": part.part_number}
                        for part in sorted(parts, key=lambda part: part.part_number)
                    ]
                },
            )
        except (BotoCoreError, ClientError) as error:
            raise StorageError("Could not complete multipart upload.") from error

    def abort_multipart(self, object_key: str, provider_upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(
                Bucket=self._bucket,
                Key=object_key,
                UploadId=provider_upload_id,
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchUpload", "NotFound"}:
                return
            raise StorageError("Could not abort multipart upload.") from error
        except BotoCoreError as error:
            raise StorageError("Could not abort multipart upload.") from error

    def head_object(self, object_key: str) -> ObjectMetadata | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=object_key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageError("Could not inspect stored object.") from error
        except BotoCoreError as error:
            raise StorageError("Could not inspect stored object.") from error
        return ObjectMetadata(
            size_bytes=int(response["ContentLength"]),
            content_type=response.get("ContentType"),
        )

    def download_file(self, object_key: str, destination: Path) -> None:
        try:
            self._client.download_file(self._bucket, object_key, str(destination))
        except (BotoCoreError, ClientError, OSError) as error:
            raise StorageError("Could not download stored object.") from error

    def upload_file(self, object_key: str, source: Path, content_type: str) -> None:
        try:
            self._client.upload_file(
                str(source),
                self._bucket,
                object_key,
                ExtraArgs={"ContentType": content_type},
            )
        except (BotoCoreError, ClientError, OSError) as error:
            raise StorageError("Could not upload object.") from error

    def delete_objects(self, object_keys: list[str]) -> set[str]:
        if not object_keys:
            return set()
        failed: set[str] = set()
        try:
            for offset in range(0, len(object_keys), 1_000):
                batch = object_keys[offset : offset + 1_000]
                response = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
                failed.update(str(item["Key"]) for item in response.get("Errors", []))
        except (BotoCoreError, ClientError, KeyError) as error:
            raise StorageError("Could not delete stored objects.") from error
        return failed

    def presign_get(self, object_key: str, expires_seconds: int) -> str:
        try:
            return cast(
                str,
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": object_key},
                    ExpiresIn=expires_seconds,
                    HttpMethod="GET",
                ),
            )
        except (BotoCoreError, ClientError) as error:
            raise StorageError("Could not authorize object playback.") from error
