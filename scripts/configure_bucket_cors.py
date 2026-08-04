"""Apply the minimum browser CORS policy to the configured private bucket."""

from __future__ import annotations

import boto3  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError

from transcriber.config import AppSettings


def main() -> None:
    settings = AppSettings()  # type: ignore[call-arg]
    client = boto3.client(
        "s3",
        endpoint_url=settings.bucket_endpoint,
        region_name=settings.bucket_region,
        aws_access_key_id=settings.bucket_access_key_id,
        aws_secret_access_key=settings.bucket_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.bucket_url_style},
        ),
    )
    try:
        client.head_bucket(Bucket=settings.bucket_name)
    except ClientError:
        if settings.app_env == "production":
            raise
        client.create_bucket(Bucket=settings.bucket_name)
    try:
        client.put_bucket_cors(
            Bucket=settings.bucket_name,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": [settings.app_public_origin],
                        "AllowedMethods": ["GET", "HEAD", "PUT"],
                        "AllowedHeaders": ["content-type"],
                        "ExposeHeaders": ["ETag"],
                        "MaxAgeSeconds": settings.presigned_url_seconds,
                    }
                ]
            },
        )
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if settings.app_env != "development" or code != "NotImplemented":
            raise


if __name__ == "__main__":
    main()
