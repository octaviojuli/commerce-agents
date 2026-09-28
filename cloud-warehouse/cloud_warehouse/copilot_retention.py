"""Offline operator cleanup for expired advisor originals; no supplier document deletion."""

import stat
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .assets import OBJECT_WRITE_LOCK


def purge(engine, root: Path, expected_database: str):
    root = root.resolve(strict=True)
    with engine.connect() as conn:
        if conn.scalar(text("SELECT current_database()")) != expected_database:
            raise ValueError("Unexpected retention database")
        if not conn.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ):
            raise ValueError("Material retention requires an offline operator")
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": OBJECT_WRITE_LOCK}):
            raise ValueError("Object storage is busy")
        conn.commit()
        removed = 0
        try:
            rows = (
                conn.execute(
                    text(
                        "SELECT id,organization_id FROM advisor_asset WHERE retain_until<=now() AND erased_at IS NULL ORDER BY retain_until,id LIMIT 1000"
                    )
                )
                .mappings()
                .all()
            )
            conn.commit()
            for row in rows:
                folder = root / str(UUID(str(row["organization_id"])))
                if folder.is_symlink() or folder.resolve().parent != root:
                    raise ValueError("Unexpected material directory")
                path = folder / str(UUID(str(row["id"])))
                if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("Unexpected material object")
                # Retire the reference first. A crash leaves encrypted orphan bytes
                # for the next run; backups never see a live reference without bytes.
                conn.execute(
                    text(
                        "UPDATE advisor_asset SET purged_at=COALESCE(purged_at,now()) WHERE id=:id AND retain_until<=now()"
                    ),
                    {"id": row["id"]},
                )
                conn.commit()
                path.unlink(missing_ok=True)
                conn.execute(
                    text("UPDATE advisor_asset SET erased_at=now() WHERE id=:id"), {"id": row["id"]}
                )
                conn.commit()
                removed += 1
            return {"retired_materials": removed}
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": OBJECT_WRITE_LOCK})
            conn.commit()
