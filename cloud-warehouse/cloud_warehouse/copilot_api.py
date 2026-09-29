"""Authenticated advisor CRM and the supplier's explicitly shared inquiry inbox."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from . import copilot_assets as assets
from . import copilot_engine as engine
from . import copilot_inquiries as inquiries
from . import copilot_ocr
from . import copilot_records as records
from . import copilot_sales as sales
from .assets import MAX_BYTES
from .persistence import Principal


def install(app, runtime, principal, store):
    router = APIRouter(prefix="/v1/copilot", tags=["advisor-copilot"])
    Actor = Annotated[Principal, Depends(principal)]

    @router.get("/customers")
    def customers(
        actor: Actor,
        query: str = Query(default="", max_length=100),
        before: UUID | None = None,
        limit: int = Query(default=30, ge=1, le=100),
    ):
        return records.customers(runtime, actor, query=query, before=before, limit=limit)

    @router.post("/customers", status_code=201)
    def customer_create(body: records.CustomerWrite, actor: Actor):
        return records.write_customer(runtime, actor, body)

    @router.post("/customers/{identifier}")
    def customer_update(identifier: UUID, body: records.CustomerWrite, actor: Actor):
        return records.write_customer(runtime, actor, body, identifier)

    @router.get("/deals")
    def deals(
        actor: Actor,
        query: str = Query(default="", max_length=100),
        before: UUID | None = None,
        limit: int = Query(default=30, ge=1, le=100),
    ):
        return records.list_deals(runtime, actor, query=query, before=before, limit=limit)

    @router.post("/deals", status_code=201)
    def deal_create(body: records.DealCreate, actor: Actor):
        return records.create_deal(runtime, actor, body)

    @router.get("/deals/{identifier}")
    def detail(identifier: UUID, actor: Actor):
        return records.detail(runtime, actor, identifier)

    @router.get("/deals/{identifier}/history")
    def history(
        identifier: UUID,
        actor: Actor,
        before: UUID | None = None,
        limit: int = Query(default=40, ge=1, le=100),
    ):
        return records.history(runtime, actor, identifier, before=before, limit=limit)

    @router.post("/deals/{identifier}/customer")
    def associate(identifier: UUID, body: records.Associate, actor: Actor):
        return records.associate(runtime, actor, identifier, body)

    @router.post("/deals/{identifier}/notes")
    def note(identifier: UUID, body: records.Note, actor: Actor):
        return records.note(runtime, actor, identifier, body)

    @router.post("/deals/{identifier}/tasks/{task_id}/complete")
    def task_done(identifier: UUID, task_id: UUID, body: records.Command, actor: Actor):
        return records.task_done(runtime, actor, identifier, task_id, body)

    @router.post("/deals/{identifier}/proposals/{record_id}")
    def adopt(identifier: UUID, record_id: UUID, body: engine.Adoption, actor: Actor):
        return engine.adopt(runtime, actor, identifier, record_id, body)

    @router.post("/deals/{identifier}/versions/{record_id}/revert")
    def revert(identifier: UUID, record_id: UUID, body: records.Command, actor: Actor):
        return engine.revert(runtime, actor, identifier, record_id, body)

    @router.post("/deals/{identifier}/confirm")
    def confirm(identifier: UUID, body: engine.Confirmation, actor: Actor):
        return engine.confirm(runtime, actor, identifier, body)

    @router.post("/deals/{identifier}/retail-quotes")
    def retail(identifier: UUID, body: sales.Retail, actor: Actor):
        return sales.retail(runtime, actor, identifier, body)

    @router.post("/deals/{identifier}/retail-quotes/{record_id}/share")
    def share(identifier: UUID, record_id: UUID, body: records.Command, actor: Actor):
        return sales.share(runtime, actor, identifier, record_id, body)

    @router.post("/deals/{identifier}/sales")
    def sale(identifier: UUID, body: sales.Sale, actor: Actor):
        return sales.sale(runtime, actor, identifier, body)

    @router.post("/deals/{identifier}/receipts")
    def receipt(identifier: UUID, body: sales.Receipt, actor: Actor):
        return sales.receipt(runtime, actor, identifier, body)

    @router.get("/inquiries")
    def inbox(
        actor: Actor,
        deal_id: UUID | None = None,
        before: UUID | None = None,
        limit: int = Query(default=30, ge=1, le=100),
    ):
        return inquiries.page(runtime, actor, deal_id=deal_id, before=before, limit=limit)

    @router.post("/deals/{identifier}/inquiries")
    def submit(identifier: UUID, body: inquiries.Submit, actor: Actor):
        return inquiries.submit(runtime, actor, identifier, body)

    @router.post("/inquiries/{identifier}/replies")
    def reply(identifier: UUID, body: inquiries.Reply, actor: Actor):
        return inquiries.reply(runtime, actor, identifier, body)

    @router.post("/deals/{identifier}/replies/{reply_id}/adopt")
    def adopt_reply(identifier: UUID, reply_id: UUID, body: records.Command, actor: Actor):
        return inquiries.adopt(runtime, actor, identifier, reply_id, body)

    @router.post("/deals/{identifier}/assets")
    async def upload(
        identifier: UUID,
        actor: Actor,
        request_id: Annotated[UUID, Form()],
        file: Annotated[UploadFile, File()],
    ):
        body = await file.read(MAX_BYTES + 1)
        return await run_in_threadpool(
            assets.upload,
            runtime,
            actor,
            store,
            identifier,
            request_id,
            file.filename or "材料",
            body,
        )

    @router.get("/assets/{identifier}")
    def download(identifier: UUID, actor: Actor):
        result = assets.download(runtime, actor, store, identifier)
        return Response(
            result["body"],
            media_type=result["media_type"],
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result['filename'])}",
            },
        )

    @router.post("/deals/{identifier}/assets/{asset_id}/recognize")
    def recognize(identifier: UUID, asset_id: UUID, body: records.Command, actor: Actor):
        return copilot_ocr.recognize(runtime, actor, store, identifier, asset_id, body)

    app.include_router(router)
