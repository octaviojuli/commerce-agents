"""Consistent private database/object bundles and restore verification for offline operators."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.conninfo import make_conninfo
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import make_url

from .admin import grant_auth, grant_runtime
from .assets import OBJECT_WRITE_LOCK, LocalObjectStore
from .persistence import engine_for


class RecoveryError(ValueError):
    """Safe operator message; database bodies and credentials never enter errors."""


class Digest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    organization: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1, le=20_000_000)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: int = Field(default=1, ge=1, le=1)
    created_at: datetime
    revision: str
    postgres_version: int
    source_fingerprint: str
    dump: Digest
    tables: dict[str, Digest]
    objects: list[ObjectRef]
    elapsed_seconds: float


def _private_directory(path: Path, *, create=False):
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RecoveryError("目录必须是真实私有目录（权限 0700）")


def _file_digest(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        mode = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
            raise RecoveryError("备份文件必须为私有普通文件")
        digest, size = hashlib.sha256(), 0
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return Digest(sha256=digest.hexdigest(), size=size)


def _pg(tool, url, *args):
    executable = shutil.which(tool)
    if not executable:
        raise RecoveryError(f"缺少 PostgreSQL 工具：{tool}")
    source = make_url(url)
    permitted = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout"}
    if set(source.query) - permitted:
        raise RecoveryError("备份连接含未支持的连接选项")
    parameters = {
        "host": source.host,
        "port": source.port,
        "user": source.username,
        "dbname": source.database,
        "connect_timeout": "10",
        **source.query,
    }
    dsn = make_conninfo(**{key: value for key, value in parameters.items() if value is not None})
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    if source.password:
        environment["PGPASSWORD"] = source.password
    # Errors may contain source row values; never forward subprocess stderr to logs.
    try:
        result = subprocess.run(
            [executable, "--dbname", dsn, *map(str, args)],
            env=environment,
            capture_output=True,
            timeout=7200,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RecoveryError(f"{tool} 无法启动或执行超时") from None
    if result.returncode:
        raise RecoveryError(f"{tool} 执行失败；未完成备份或恢复，请检查受保护的数据库日志")


def _snapshot_connection(engine):
    return engine.connect().execution_options(isolation_level="REPEATABLE READ")


def _settings(conn):
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SET LOCAL timezone='UTC'"))
    conn.execute(text("SET LOCAL datestyle='ISO, YMD'"))
    conn.execute(text("SET LOCAL intervalstyle='postgres'"))
    conn.execute(text("SET LOCAL bytea_output='hex'"))
    conn.execute(text("SET LOCAL extra_float_digits=3"))
    conn.execute(text("SET LOCAL row_security=off"))


def _tables(conn):
    tables = conn.execute(
        text("""SELECT c.relname, ARRAY(
      SELECT a.attname FROM pg_index i JOIN unnest(i.indkey) WITH ORDINALITY k(attnum,n) ON true
      JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
      WHERE i.indrelid=c.oid AND i.indisprimary ORDER BY k.n) AS keys
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname""")
    ).all()
    result = {}
    quote = conn.dialect.identifier_preparer.quote
    for name, keys in tables:
        if not keys:
            raise RecoveryError("数据表缺少稳定主键，不能核对备份")
        digest, count = hashlib.sha256(), 0
        query = f"SELECT row_to_json(t)::text FROM public.{quote(name)} AS t ORDER BY " + ",".join(
            map(quote, keys)
        )
        # Import rows contain up to 10 MB of source bytes; keep memory bounded.
        for body in conn.execution_options(yield_per=5).execute(text(query)).scalars():
            digest.update(body.encode())
            digest.update(b"\n")
            count += 1
        result[name] = Digest(sha256=digest.hexdigest(), size=count)
    return result


def _objects(conn):
    query = "SELECT id,supplier_org_id,file_hash,byte_size FROM document_asset"
    if conn.scalar(text("SELECT to_regclass('public.document_media') IS NOT NULL")):
        query += " UNION ALL SELECT id,supplier_org_id,file_hash,byte_size FROM document_media"
    if conn.scalar(text("SELECT to_regclass('public.advisor_asset') IS NOT NULL")):
        query += " UNION ALL SELECT id,organization_id,file_hash,byte_size FROM advisor_asset"
        if conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='advisor_asset' AND column_name='purged_at')"
            )
        ):
            query += " WHERE purged_at IS NULL"
    return [
        ObjectRef(
            id=row.id, organization=row.supplier_org_id, sha256=row.file_hash, size=row.byte_size
        )
        for row in conn.execute(text(query + " ORDER BY id"))
    ]


def _ledger(conn):
    mismatches = conn.scalar(
        text("""SELECT count(*) FROM inventory_pool p LEFT JOIN (
      SELECT pool_id,sum(delta_total) AS total,sum(delta_sold) AS sold,
        sum(delta_blocked) AS blocked,count(*) AS entries FROM inventory_movement GROUP BY pool_id
      ) m ON m.pool_id=p.id WHERE p.total<>coalesce(m.total,0) OR p.sold<>coalesce(m.sold,0)
        OR p.blocked<>coalesce(m.blocked,0) OR p.held<>0 OR p.version<>coalesce(m.entries,0)""")
    )
    if mismatches:
        raise RecoveryError("库存余额或版本与不可改写流水不一致")
    return conn.scalar(text("SELECT count(*) FROM inventory_pool"))


def _write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        path.chmod(0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def _copy_objects(refs, source, destination):
    for item in refs:
        body = _read_object(source, item)
        destination.put(item.organization, item.id, body)


def _read_object(store, item):
    directory = store.root / str(item.organization)
    _private_directory(directory)
    mode = (directory / str(item.id)).lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
        raise RecoveryError("附件必须为私有普通文件")
    body = store.read(item.organization, item.id, item.sha256)
    if len(body) != item.size:
        raise RecoveryError("附件长度与备份索引不一致")
    return body


def backup(url: str, object_root: Path, destination: Path) -> Manifest:
    """Export exactly one DB snapshot and the immutable files it references."""
    started = time.monotonic()
    _private_directory(object_root)
    _private_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise RecoveryError("备份目标已存在，禁止覆盖")
    staging = destination.parent / f".partial-{uuid4()}"
    staging.mkdir(mode=0o700)
    engine = engine_for(url)
    try:
        with _snapshot_connection(engine) as conn, conn.begin():
            _settings(conn)
            if not conn.scalar(
                text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
                {"key": OBJECT_WRITE_LOCK},
            ):
                raise RecoveryError("对象清理正在运行，备份未开始，请稍后重试")
            snapshot = conn.scalar(text("SELECT pg_export_snapshot()"))
            created = conn.scalar(text("SELECT transaction_timestamp()"))
            revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
            version = int(conn.scalar(text("SHOW server_version_num")))
            tables, objects = _tables(conn), _objects(conn)
            _ledger(conn)
            # The exporting transaction remains alive until pg_dump imports the snapshot.
            dump = staging / "database.dump"
            _pg(
                "pg_dump",
                url,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--snapshot",
                snapshot,
                "--file",
                dump,
            )
            dump.chmod(0o600)
            _copy_objects(
                objects, LocalObjectStore(object_root), LocalObjectStore(staging / "objects")
            )
        source = make_url(url)
        fingerprint = hashlib.sha256(
            json.dumps([source.host, source.port, source.database]).encode()
        ).hexdigest()
        manifest = Manifest(
            created_at=created,
            revision=revision,
            postgres_version=version,
            source_fingerprint=fingerprint,
            dump=_file_digest(dump),
            tables=tables,
            objects=objects,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
        _write_json(staging / "manifest.json", manifest.model_dump(mode="json"))
        staging.rename(destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return manifest
    finally:
        engine.dispose()
        if staging.exists():
            shutil.rmtree(staging)


def inspect_bundle(bundle: Path) -> Manifest:
    _private_directory(bundle)
    path = bundle / "manifest.json"
    metadata = _file_digest(path)
    if metadata.size > 64 * 1024 * 1024:
        raise RecoveryError("备份清单超出大小限制")
    try:
        manifest = Manifest.model_validate_json(path.read_bytes())
    except ValueError:
        raise RecoveryError("备份清单格式不正确") from None
    if _file_digest(bundle / "database.dump") != manifest.dump:
        raise RecoveryError("数据库备份长度或哈希不一致")
    if len({(item.organization, item.id) for item in manifest.objects}) != len(manifest.objects):
        raise RecoveryError("附件索引有重复记录")
    _private_directory(bundle / "objects")
    store = LocalObjectStore(bundle / "objects")
    for item in manifest.objects:
        _read_object(store, item)
    return manifest


def verify(url: str, object_root: Path, manifest: Manifest) -> dict:
    """Check exact restored content before any API, worker or session cleanup runs."""
    engine = engine_for(url)
    try:
        with _snapshot_connection(engine) as conn, conn.begin():
            _settings(conn)
            if conn.scalar(text("SELECT version_num FROM alembic_version")) != manifest.revision:
                raise RecoveryError("恢复的迁移版本不一致")
            if _tables(conn) != manifest.tables:
                raise RecoveryError("恢复后的表数量或内容哈希不一致")
            if _objects(conn) != manifest.objects:
                raise RecoveryError("恢复后的附件索引不一致")
            pools = _ledger(conn)
            # Catalog all tenant-bearing tables, not a hand-maintained subset.
            unprotected = conn.scalar(
                text("""SELECT count(DISTINCT c.oid) FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
              WHERE n.nspname='public' AND c.relkind='r' AND a.attname IN ('supplier_org_id','organization_id')
              AND c.relname<>'membership' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)""")
            )
            if unprotected:
                raise RecoveryError("恢复后的组织隔离策略未强制启用")
        _private_directory(object_root)
        store = LocalObjectStore(object_root)
        for item in manifest.objects:
            _read_object(store, item)
        return {
            "verified": True,
            "tables": len(manifest.tables),
            "rows": sum(item.size for item in manifest.tables.values()),
            "objects": len(manifest.objects),
            "object_bytes": sum(item.size for item in manifest.objects),
            "inventory_pools_reconstructed": pools,
            "revision": manifest.revision,
        }
    finally:
        engine.dispose()


def restore(
    url: str,
    bundle: Path,
    object_root: Path,
    *,
    expected_database: str,
    runtime_role: str,
    auth_role: str,
) -> dict:
    """Restore to a pre-created empty offline database and a new private object directory.

    No --clean, DROP or automatic retry is used. After a post-restore failure the
    target remains offline for diagnosis; the existing application is never touched.
    """
    started = time.monotonic()
    target = make_url(url)
    if not expected_database or target.database != expected_database:
        raise RecoveryError("目标库名必须与明确指定的恢复库一致")
    if runtime_role == auth_role or target.username in (runtime_role, auth_role):
        raise RecoveryError("恢复管理员、业务和认证角色必须分别配置")
    _private_directory(object_root.parent)
    if object_root.exists() or object_root.is_symlink():
        raise RecoveryError("恢复附件目录必须是未使用的新目录")
    manifest = inspect_bundle(bundle)
    engine = engine_for(url)
    try:
        with engine.connect() as conn:
            nonempty = conn.scalar(
                text("""SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
              AND c.relkind IN ('r','p','v','m','S','f')) OR EXISTS(SELECT 1 FROM pg_proc p
              JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))""")
            )
            if nonempty:
                raise RecoveryError("恢复目标库非空，拒绝覆盖")
            for role in (runtime_role, auth_role):
                row = conn.execute(
                    text(
                        "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=:role"
                    ),
                    {"role": role},
                ).one_or_none()
                if not row or any(row):
                    raise RecoveryError("恢复所需业务角色不存在或权限过高")
        # Only database administrators should connect until verification and grants finish.
        with engine.begin() as conn:
            quoted = conn.dialect.identifier_preparer.quote(expected_database)
            conn.execute(text(f"REVOKE CONNECT ON DATABASE {quoted} FROM PUBLIC"))
            for role in (runtime_role, auth_role):
                conn.execute(
                    text(
                        f"REVOKE CONNECT ON DATABASE {quoted} FROM {conn.dialect.identifier_preparer.quote(role)}"
                    )
                )
        _pg(
            "pg_restore",
            url,
            "--no-owner",
            "--no-acl",
            "--single-transaction",
            "--exit-on-error",
            bundle / "database.dump",
        )
        _copy_objects(
            manifest.objects, LocalObjectStore(bundle / "objects"), LocalObjectStore(object_root)
        )
        result = verify(url, object_root, manifest)
        grant_runtime(engine, runtime_role)
        grant_auth(engine, auth_role)
        result.update(
            elapsed_seconds=round(time.monotonic() - started, 3), activation_required=True
        )
        _write_json(object_root.parent / f"restore-report-{uuid4()}.json", result)
        return result
    finally:
        engine.dispose()
