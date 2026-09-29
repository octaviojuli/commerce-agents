"""Serialized operator backup attempts with private status and explicit space guards."""

import fcntl
import hashlib
import json
import os
import shutil
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import make_url

from . import recovery
from .persistence import engine_for


class BackupJobError(ValueError):
    """Fixed codes only; no paths, credentials, SQL or source exception messages."""


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    source: str = Field(pattern=r"^[a-f0-9]{64}$")
    objects: str = Field(pattern=r"^[a-f0-9]{64}$")


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    attempt_id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["running", "succeeded", "failed"]
    error: Literal["SPACE_GUARD", "BACKUP_FAILED"] | None = None
    last_success_at: datetime | None = None
    last_bundle: str | None = Field(default=None, pattern=r"^run-[0-9a-f]{32}$")
    last_bundle_bytes: int | None = Field(default=None, ge=0)


def _read(path, model):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        mode = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
            raise BackupJobError("INVALID_PRIVATE_STATE")
        data = handle.read(32769)
        if len(data) > 32768:
            raise BackupJobError("INVALID_PRIVATE_STATE")
    return model.model_validate_json(data)


def _write(path, value):
    temporary = path.with_name(f".state-{uuid4().hex}")
    try:
        recovery._write_json(temporary, value.model_dump(mode="json"))
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(root, *, create):
    recovery._private_directory(root)
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    descriptor = os.open(root / "job.lock", flags, 0o600)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
            raise BackupJobError("INVALID_PRIVATE_LOCK")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _size(root):
    total = 0
    for parent, folders, files in os.walk(root):
        for name in folders:
            recovery._private_directory(Path(parent) / name)
        for name in files:
            info = (Path(parent) / name).lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise BackupJobError("INVALID_PRIVATE_FILE")
            total += info.st_size
    return total


def _estimate(url, expected_database):
    engine = engine_for(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL row_security=off"))
            actual = conn.scalar(text("SELECT current_database()"))
            if actual != expected_database:
                raise BackupJobError("DATABASE_MISMATCH")
            size = conn.scalar(text("SELECT pg_database_size(current_database())"))
            objects = conn.scalar(text("SELECT coalesce(sum(byte_size),0) FROM document_asset"))
            # Conservative preflight estimate, not a hard filesystem quota. Concurrent
            # growth and dump overhead can differ; never delete a completed bundle.
            return int((size + objects) * 1.25) + 64 * 1024 * 1024
    finally:
        engine.dispose()


def run(url, objects: Path, root: Path, *, expected_database, max_bytes, reserve_bytes):
    if any(type(v) is not int or v < 1 for v in (max_bytes, reserve_bytes)):
        raise BackupJobError("INVALID_SPACE_POLICY")
    source = make_url(url)
    if source.drivername != "postgresql+psycopg":
        raise BackupJobError("INVALID_DATABASE_DRIVER")
    if not expected_database or source.database != expected_database:
        raise BackupJobError("DATABASE_MISMATCH")
    recovery._private_directory(objects)
    recovery._private_directory(root)
    if objects.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        objects.resolve()
    ):
        raise BackupJobError("OVERLAPPING_DIRECTORIES")
    fingerprint = hashlib.sha256(
        json.dumps([source.host, source.port, source.database]).encode()
    ).hexdigest()
    object_fingerprint = hashlib.sha256(str(objects.resolve()).encode()).hexdigest()
    with _lock(root, create=True) as acquired:
        if not acquired:
            return {"status": "already_running"}
        identity_path = root / "job.json"
        if identity_path.exists() or identity_path.is_symlink():
            identity = _read(identity_path, Identity)
            if identity.source != fingerprint or identity.objects != object_fingerprint:
                raise BackupJobError("BACKUP_ROOT_MISMATCH")
        else:
            # Require a dedicated root; preserve all pre-existing operator backups.
            if set(p.name for p in root.iterdir()) != {"job.lock"}:
                raise BackupJobError("BACKUP_ROOT_NOT_EMPTY")
            identity = Identity(job_id=uuid4(), source=fingerprint, objects=object_fingerprint)
            _write(identity_path, identity)
        state_path = root / "state.json"
        previous = _read(state_path, State) if state_path.exists() else None
        if previous and previous.job_id != identity.job_id:
            raise BackupJobError("BACKUP_ROOT_MISMATCH")
        state = State(
            job_id=identity.job_id,
            attempt_id=uuid4(),
            started_at=datetime.now(UTC),
            status="running",
            last_success_at=previous.last_success_at if previous else None,
            last_bundle=previous.last_bundle if previous else None,
            last_bundle_bytes=previous.last_bundle_bytes if previous else None,
        )
        _write(state_path, state)
        try:
            estimate = _estimate(url, expected_database)
            if _size(root) + estimate > max_bytes or shutil.disk_usage(root).free < (
                reserve_bytes + estimate
            ):
                raise BackupJobError("SPACE_GUARD")
            destination = root / ("run-" + state.attempt_id.hex)
            manifest = recovery.backup(url, objects, destination)
            if recovery.inspect_bundle(destination) != manifest:
                raise BackupJobError("BACKUP_FAILED")
            state.last_success_at = manifest.created_at
            state.last_bundle = destination.name
            state.last_bundle_bytes = _size(destination)
            state.status = "succeeded"
            state.finished_at = datetime.now(UTC)
            _write(state_path, state)
        except Exception as error:
            # Interrupted processes leave a running marker. status() checks the OS lock
            # rather than treating that marker as evidence of a live process.
            state.status = "failed"
            state.error = "SPACE_GUARD" if str(error) == "SPACE_GUARD" else "BACKUP_FAILED"
            state.finished_at = datetime.now(UTC)
            _write(state_path, state)
            raise BackupJobError(state.error) from None
        return state.model_dump(mode="json")


def status(root: Path, *, max_age_seconds: int):
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise BackupJobError("INVALID_MAX_AGE")
    with _lock(root, create=False) as acquired:
        identity = _read(root / "job.json", Identity)
        state_path = root / "state.json"
        state = _read(state_path, State) if state_path.exists() else None
        if state is None:
            return {"status": "not_started" if acquired else "running", "fresh": False}
        if state.job_id != identity.job_id:
            raise BackupJobError("BACKUP_ROOT_MISMATCH")
        age = (
            (datetime.now(UTC) - state.last_success_at).total_seconds()
            if state.last_success_at
            else None
        )
        observed = (
            "running"
            if not acquired
            else "interrupted"
            if state.status == "running"
            else state.status
        )
        bundle = root / state.last_bundle if state.last_bundle else None
        present = bool(bundle and bundle.is_dir() and not bundle.is_symlink())
        if present:
            recovery._private_directory(bundle)
            present = all(
                (bundle / name).is_file() and not (bundle / name).is_symlink()
                for name in ("manifest.json", "database.dump")
            )
        return {
            **state.model_dump(mode="json"),
            "status": observed,
            "snapshot_age_seconds": age,
            "fresh": present and age is not None and 0 <= age <= max_age_seconds,
            "bundle_present": present,
            "bundle_reverified": False,
            "restore_verified": False,
        }
