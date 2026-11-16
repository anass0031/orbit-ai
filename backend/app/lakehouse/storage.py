"""Object storage: S3-compatible (MinIO) or a local folder, behind the same interface.

Key layout in the bucket:
    landing/<import_id>/<original file name>          raw uploaded file, kept as-is
    bronze/<dataset>/v<N>/part-0.parquet              Bronze dataset version (immutable)
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings


class ObjectStorage(ABC):
    backend: str

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> int: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def uri(self, key: str) -> str: ...

    @abstractmethod
    def ping(self) -> str:
        """Raise when the storage is unreachable; return a short description otherwise."""


class LocalStorage(ObjectStorage):
    backend = "local"

    def __init__(self, root: Path):
        self.root = (root / "objects").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Invalid object key")
        return p

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return len(data)

    def get_bytes(self, key):
        return self._p(key).read_bytes()

    def exists(self, key):
        return self._p(key).exists()

    def delete(self, key):
        p = self._p(key)
        if p.exists():
            p.unlink()

    def uri(self, key):
        return f"file://{self._p(key).as_posix()}"

    def ping(self):
        return f"local folder {self.root}"


class S3Storage(ObjectStorage):
    backend = "s3"

    def __init__(self):
        import boto3
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=5, read_timeout=60),
        )
        self._bucket_ready = False
        self._lock = threading.Lock()

    def ensure_bucket(self):
        if self._bucket_ready:
            return
        with self._lock:
            from botocore.exceptions import ClientError

            try:
                self.client.head_bucket(Bucket=self.bucket)
            except ClientError:
                self.client.create_bucket(Bucket=self.bucket)
            self._bucket_ready = True

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return len(data)

    def get_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def uri(self, key):
        return f"s3://{self.bucket}/{key}"

    def ping(self):
        self.ensure_bucket()
        return f"bucket {self.bucket}"


_storage: ObjectStorage | None = None
_storage_lock = threading.Lock()


def get_storage() -> ObjectStorage:
    global _storage
    with _storage_lock:
        if _storage is None:
            s = get_settings()
            _storage = S3Storage() if s.storage_backend == "s3" else LocalStorage(s.resolved_data_dir)
        return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
