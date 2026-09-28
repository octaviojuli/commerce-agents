"""Operator-only, journaled collection of old unreferenced local document objects."""

import json
import os
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .assets import OBJECT_WRITE_LOCK


class CollectionError(ValueError):
    """Only fixed operator messages, never database credentials or file contents."""


def _uuid(value):
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _kind(name):
    if _uuid(name):
        return "object"
    if name.startswith(".") and name.endswith(".tmp") and _uuid(name[1:-4]):
        return "temporary"
    return None


def _directory(stack, name, *, parent=None):
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stack.callback(os.close, descriptor)
    if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
        raise CollectionError("对象目录必须仅允许服务账号访问")
    return descriptor


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def collect(engine, root: Path, *, expected_database: str, journal: Path, apply=False, days=7):
    """Dry-run unless explicit apply; journal creation is exclusive and always private.

    Requires a dedicated object root for this database and all writers upgraded to
    the shared transaction lock. Restore must remain offline during collection.
    All historical document_asset rows are roots, regardless of publication status.
    """
    if type(days) is not int or days < 1:
        raise CollectionError("保留期必须为至少一天的整数天数")
    if not expected_database:
        raise CollectionError("必须明确提供目标数据库名称")
    # Reject a symlink at the supplied root; do not resolve it away before opening.
    root = Path(os.path.abspath(root))
    journal = Path(os.path.abspath(journal))
    if journal.resolve().is_relative_to(root.resolve()):
        raise CollectionError("清理日志必须保存在对象目录之外")
    cutoff = time.time() - days * 86400
    with ExitStack() as stack, engine.begin() as conn:
        name = conn.scalar(text("SELECT current_database()"))
        if name != expected_database:
            raise CollectionError("数据库名称与预期不符")
        # A tenant-limited query must never be mistaken for all object references.
        conn.execute(text("SET LOCAL row_security=off"))
        privileged = conn.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        )
        if not privileged:
            raise CollectionError("对象清理需要专用离线管理连接")
        if not conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": OBJECT_WRITE_LOCK}
        ):
            raise CollectionError("文档写入或其他清理正在运行，请稍后重试")
        reference_query = "SELECT supplier_org_id,id FROM document_asset"
        if conn.scalar(text("SELECT to_regclass('public.document_media') IS NOT NULL")):
            reference_query += " UNION ALL SELECT supplier_org_id,id FROM document_media"
        if conn.scalar(text("SELECT to_regclass('public.advisor_asset') IS NOT NULL")):
            reference_query += " UNION ALL SELECT organization_id,id FROM advisor_asset"
            if conn.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='advisor_asset' AND column_name='purged_at')"
                )
            ):
                reference_query += " WHERE purged_at IS NULL"
        references = {
            (str(row.supplier_org_id), str(row.id)) for row in conn.execute(text(reference_query))
        }
        directory = _directory(stack, root)
        found, candidates = set(), []
        counts = {"referenced": 0, "recent": 0, "unrecognized": 0, "candidates": 0, "bytes": 0}
        for organization in sorted(os.listdir(directory)):
            info = os.stat(organization, dir_fd=directory, follow_symlinks=False)
            if not _uuid(organization) or not stat.S_ISDIR(info.st_mode):
                counts["unrecognized"] += 1
                continue
            with ExitStack() as folder_stack:
                folder = _directory(folder_stack, organization, parent=directory)
                for asset in sorted(os.listdir(folder)):
                    info = os.stat(asset, dir_fd=folder, follow_symlinks=False)
                    kind = _kind(asset)
                    if not kind or not stat.S_ISREG(info.st_mode):
                        counts["unrecognized"] += 1
                        continue
                    if (organization, asset) in references:
                        found.add((organization, asset))
                        counts["referenced"] += 1
                    elif max(info.st_mtime, info.st_ctime) >= cutoff:
                        counts["recent"] += 1
                    else:
                        candidates.append((organization, asset, _identity(info), kind))
                        counts["candidates"] += 1
                        counts["bytes"] += info.st_size
        missing = len(references - found)
        # Fail closed on an empty/wrong database or incomplete/mismatched object mount.
        if apply and (not references or missing):
            raise CollectionError("缺少完整的已引用对象，不能确认数据库与对象目录对应；仅允许检查")
        descriptor = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        handle = stack.enter_context(os.fdopen(descriptor, "w"))
        parent = os.open(journal.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

        def record(value):
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        record(
            {
                "event": "start",
                "database": name,
                "root": str(root),
                "retention_cutoff_unix": cutoff,
                "apply": apply,
                "days": days,
                "missing": missing,
                **counts,
            }
        )
        deleted = removed_bytes = 0
        for organization, asset, identity, kind in candidates:
            record(
                {
                    "event": "candidate",
                    "organization": organization,
                    "asset": asset,
                    "kind": kind,
                    "bytes": identity[2],
                }
            )
            if not apply:
                continue
            with ExitStack() as folder_stack:
                folder = _directory(folder_stack, organization, parent=directory)
                info = os.stat(asset, dir_fd=folder, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or _identity(info) != identity:
                    raise CollectionError("对象在检查后发生变化；已停止，请查看清理日志")
                # Candidate is journaled before unlink; an interrupted last entry must
                # be reconciled by another scan, never assumed to have been removed.
                os.unlink(asset, dir_fd=folder)
                os.fsync(folder)
                deleted += 1
                removed_bytes += info.st_size
                record({"event": "deleted", "organization": organization, "asset": asset})
        result = {
            "apply": apply,
            "missing": missing,
            **counts,
            "deleted": deleted,
            "removed_bytes": removed_bytes,
        }
        record({"event": "complete", **result})
        return result
