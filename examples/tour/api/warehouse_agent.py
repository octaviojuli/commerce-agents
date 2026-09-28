"""Warehouse advisor tools use the same durable requirements as workbench actions."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from cloud_warehouse import advisor_actions, advisor_flow, trip_brief
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from commerce_common.presentation import PresentationExtension
from shopping_agent.config import ShoppingAgentConfig
from shopping_agent.fencing import STOREFRONT_FENCE
from shopping_agent_runtime import ShoppingAgent


class WarehousePresentationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = Field(default=None, max_length=160)


class DeparturePage(WarehousePresentationPayload):
    product_id: str
    after: str | None = Field(default=None, max_length=1000)
    limit: int = Field(default=12, ge=1, le=25)


class DepartureQuote(WarehousePresentationPayload):
    departure_id: str
    offer_id: UUID | None = None


class OfferPage(WarehousePresentationPayload):
    departure_id: str
    after: UUID | None = None
    limit: int = Field(default=12, ge=1, le=25)


class UpdateBrief(WarehousePresentationPayload):
    expected_version: int = Field(ge=0)
    fields: trip_brief.Requirements


class SearchRoutes(WarehousePresentationPayload):
    after: str | None = Field(default=None, max_length=1000)
    limit: int = Field(default=3, ge=1, le=3)


class WarehouseAgentConfig(ShoppingAgentConfig):
    def absent_tools(self) -> frozenset[str]:
        return super().absent_tools() | {
            "search_products",
            "present_products",
            "present_comparison",
        }


def note(context, result):
    # Preserve identifiers and monetary facts before bounding descriptive prose.
    compact = dict(result)
    for key in ("service_description", "offer_name"):
        if key in compact:
            compact[key] = STOREFRONT_FENCE.sanitize_text(str(compact[key] or ""), 300)
    if "items" in compact:
        compact["items"] = [
            {
                k: v
                for k, v in item.items()
                if k in ("product_id", "title", "offer_id", "name", "attributes", "match_reasons")
            }
            for item in compact["items"]
            if item.get("attributes", {}).get("capacity_match") != "out_of_window"
        ]
    context.notes.append(STOREFRONT_FENCE.fence_payload(compact, max_chars=24000))


async def action(context, name, **kwargs):
    saved = await run_in_threadpool(advisor_actions.snapshot, context.backend, context.session)
    await run_in_threadpool(
        advisor_flow.require_choice,
        context.backend.engine,
        context.backend.actor,
        UUID(context.session.session_id),
        name,
        kwargs.get("product_id"),
        saved,
    )
    result = await advisor_actions.perform(
        context.backend,
        context.session,
        context.state,
        advisor_actions.Action(action=name, expected_version=saved["version"], **kwargs),
    )
    payload = result["payload"]
    # The UI receives a one-time link; neither model notes nor durable events retain it.
    note(context, {key: value for key, value in payload.items() if key != "token"})
    return payload


async def departures(payload, context):
    return await action(
        context,
        "departures",
        product_id=payload.product_id,
        after=str(payload.after) if payload.after else None,
        limit=payload.limit,
    )


async def quote(payload, context):
    return await action(
        context, "quote", product_id=payload.departure_id, offer_id=payload.offer_id
    )


async def offers(payload, context):
    return await action(
        context,
        "offers",
        product_id=payload.departure_id,
        after=str(payload.after) if payload.after else None,
        limit=payload.limit,
    )


async def update(payload, context):
    result = await run_in_threadpool(
        trip_brief.patch,
        context.backend.engine,
        context.backend.actor,
        UUID(context.session.session_id),
        trip_brief.BriefPatch(
            expected_version=payload.expected_version,
            fields=payload.fields.model_dump(mode="json", exclude_unset=True),
        ),
        model=True,
    )
    note(context, result)
    return result


async def search_routes(payload, context):
    return await action(
        context,
        "search_routes",
        after=str(payload.after) if payload.after else None,
        limit=payload.limit,
    )


async def share_quote(payload, context):
    return await action(context, "share_quote")


class ReadItinerary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    departure_id: str | None = None
    section: Literal["overview", "day", "terms"] = "overview"
    day: int | None = Field(default=None, ge=1, le=365)
    offset: int = Field(default=0, ge=0, le=1000)


async def itinerary(payload, context):
    from cloud_warehouse.itinerary_reads import read

    backend = context.backend
    result = await run_in_threadpool(read, backend.engine, backend.actor, **payload.model_dump())
    context.notes.append(STOREFRONT_FENCE.fence_payload(result, max_chars=24000))
    return result


EXTENSIONS = tuple(
    PresentationExtension(
        name=name,
        component=component,
        payload_model=model,
        input_schema=model.model_json_schema(),
        enrich=enrich,
        description=description,
    )
    for name, component, model, enrich, description in (
        (
            "read_route_itinerary",
            "warehouse_itinerary",
            ReadItinerary,
            itinerary,
            "顾问要求了解或比较已展示线路时，按概览、指定日期或费用条款读取已发布行程。按 next_offset 继续读取，不能据未显示的内容作判断。此工具不选择线路、不查询团期或价格。",
        ),
        (
            "present_warehouse_departures",
            "warehouse_departures",
            DeparturePage,
            departures,
            "仅在顾问明确选定一条已展示线路并要求查看团期后调用；展示过线路不代表已选中。首次找线路不得调用，也不得逐条查团期。日期与人数来自需求单，按 next_cursor 分页。",
        ),
        (
            "present_warehouse_quote",
            "warehouse_quote",
            DepartureQuote,
            quote,
            "按持久化需求单询价已展示团期；缺项返回清单，一个方案自动选用，多方案先读取并选择。",
        ),
        (
            "present_warehouse_offers",
            "warehouse_offers",
            OfferPage,
            offers,
            "读取已展示团期的方案名称与服务说明，多个方案由顾问选择，共享名额。",
        ),
        (
            "update_trip_brief",
            "warehouse_brief",
            UpdateBrief,
            update,
            "记录本轮新需求。仅提交修改字段。destinations 是必须覆盖的目的地：德法意瑞即德国、法国、意大利、瑞士四国均需要；只有明确比如、例如、可选时才写 destination_examples。不去的国家写 excluded_destinations。欧洲、西欧等背景区域写 destination_regions；已有具体必选国家时，不再把区域叠加为硬条件。服务端会依据本轮原话校正。said 必须带本轮原话 evidence；inferred 必须带 hint；人工来源不允许模型使用。",
        ),
        (
            "search_routes",
            "warehouse_routes",
            SearchRoutes,
            search_routes,
            "按已保存需求单检索并直接展示最多三条线路卡片。服务端组合必需目的地与出发日期；不可另加搜索词、举例国家或临时日期。其余结果由卡片的查看更多加载；仅在顾问明确要求下一页时使用 next_cursor。",
        ),
        (
            "share_quote",
            "warehouse_share",
            WarehousePresentationPayload,
            share_quote,
            "顾问明确要求发给客人时生成当前有效报价的对客分享凭据；工作台提供客户链接。",
        ),
    )
)


def configuration(**overrides):
    values = dict(
        assistant_name="旅游云仓顾问助手",
        enable_cart=False,
        enable_orders=False,
        enable_policies=False,
        enable_fulfillment=False,
        enable_memory=False,
        enable_web_search=False,
        close_on_presentation=True,
        max_context_chars=80000,
        domain_search_notes=(
            "1. 每轮先用 update_trip_brief 记录新需求；若返回 conflicts，说明保留了顾问修改，请顾问选择，不要重试覆盖；said 的 evidence 必须来自本轮原话，日期换算和占床推断用 inferred 并写明依据。"
            "2. 以当前日期理解未写年份的时间；四人家庭不代表成人儿童构成。两间双人房供四人可推断儿童占床，但必须标记待确认。"
            "3. 找线路阶段使用 search_routes 从需求单检索一次，工具已展示卡片，不再生成一套线路列表；已有候选且顾问选线时不重新检索。window 是可出发日期范围，不限制返程日或旅行天数；不得拿窗口跨度与线路天数比较。days 仅记录客人明确表达的旅行时长。举例国家只参与排序与理由，不要求全部覆盖；未有结果先说明当前条件，征得顾问同意后更新需求，不自行扩大日期或减少国家。线路卡片的 destination_facts 和 match_reasons 标明依据；名称匹配只能称候选，不能承诺全部国家已确认覆盖或四人可以报名。首轮仅一句简短结果说明与选线引导，不重复列卡片、不罗列团期、不追问询价阶段资料。"
            "4. 严格按找线路→顾问选一条→看该线路团期→顾问选一个团期→询价推进。首次只给目的地、时间、人数和天数时，检索线路即可；不自行选线，不查询或展示团期，不追问成人儿童构成、年龄、房型等报价资料。顾问点击按钮或明确说选第几条后才推进一步，不能在同轮自动走完后续步骤。"
            "5. 找线路后用一两句话说明匹配与必要待核实项，并请顾问选线，不复述卡片，不重复总结。当前人数只是总人数时保持未知构成，不补成成人。日期和天数推断轻量提醒即可。只有进入询价且缺项时才一次追问一件。不得要求重新检索已展示的候选；分页未完成只能说暂未找到。窗口外团期已折叠，不在回复里展开或推荐。"
            "6. 行程比较先读适用的已复核文档；标题和营销描述仅是名称所述，不据此推断游览范围、节奏、酒店、航班或包含费用；未复核只列已知事实，不用行程适合度做推荐。"
            "7. 多方案让顾问选择；报价区分市场价和同行结算价，缺项只列已知部分，实际日期和标称天数冲突时标记待确认。"
            "8. 用户要求发给客人时使用 share_quote；只生成对客链接，客户确认和线下备注不代表系统已占位。"
        ),
        brand_voice="所有对话文字均用简洁中文，不输出英文思考或查找过程。库存只报告有位、无位或待确认，不报告或推算具体余位。价格或库存为空时明确说未知，不说免费、无库存或已售罄。询价前确认人数、儿童年龄与所需房型，不默认替客户决定。市场价与采购同业价分别标明，报价缺项时只说明已知部分，不声称全包总价。没有预约、订单或支付能力，不承诺占位。历史报价必须重新查询才可作为当前报价。name_origin、description_origin 或 product_name_origin 为 warehouse_display 时，内容是云仓展示补充，不得当成上游原文或已复核行程。",
    )
    values.update(overrides)
    # These capability constraints cannot be relaxed by deployment configuration.
    values.update(
        enable_cart=False,
        enable_orders=False,
        enable_policies=False,
        enable_fulfillment=False,
        enable_memory=False,
        enable_web_search=False,
    )
    return WarehouseAgentConfig(**values)


def build_agent(backend: WarehouseAdvisorBackend, *, client=None, config=None):
    return ShoppingAgent(
        backend=backend,
        config=configuration(**(config or {})),
        client=client,
        extra_presentation_tools=EXTENSIONS,
    )
