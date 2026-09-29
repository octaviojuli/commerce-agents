"""Private immutable objects, addressed only by server-generated organization and asset IDs."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

MAX_BYTES = 20_000_000
# Shared by document writers and the offline garbage collector, within one database.
OBJECT_WRITE_LOCK = 0x57415245484F5553


class ObjectStore(Protocol):
    def put(self, organization: UUID, asset: UUID, body: bytes) -> None: ...
    def read(self, organization: UUID, asset: UUID, digest: str) -> bytes: ...


class LocalObjectStore:
    """Single-host private volume implementation; never mount this root as static web content."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise ValueError("文档存储目录必须仅允许服务账号访问")

    def _directory(self, organization):
        path = self.root / str(UUID(str(organization)))
        path.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("无效的文档存储路径")
        return path

    def put(self, organization, asset, body):
        if not 0 < len(body) <= MAX_BYTES:
            raise ValueError("文档大小无效")
        path = self._directory(organization) / str(UUID(str(asset)))
        temp = path.parent / f".{uuid4()}.tmp"
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.link(temp, path)  # Immutable: an existing object is never overwritten.
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temp.unlink(missing_ok=True)

    def read(self, organization, asset, digest):
        path = self._directory(organization) / str(UUID(str(asset)))
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError("文档对象不是普通文件")
            body = file.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("文档完整性校验失败")
        return body
