"""Private immutable derived images with the same source and publication scope as routes."""

from sqlalchemy import text

from .integrations import SourceError
from .persistence import Forbidden, require_role, transaction


def read(engine, actor, store, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin", "supplier_admin", "product_editor", "auditor")
        row = (
            conn.execute(
                text("SELECT id,supplier_org_id,file_hash FROM document_media WHERE id=:id"),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            raise Forbidden("图片不存在或当前无访问权限")
        try:
            return store.read(row["supplier_org_id"], row["id"], row["file_hash"])
        except (OSError, ValueError) as error:
            raise SourceError("DOCUMENT_OBJECT_UNAVAILABLE") from error
