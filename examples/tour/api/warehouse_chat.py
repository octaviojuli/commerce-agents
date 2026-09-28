"""Compose both original agent runtimes with authenticated warehouse backends."""

import os

from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.merchant import WarehouseMerchantBackend
from commerce_common.streaming import AgentEvent
from merchant_agent.config import MerchantAgentConfig
from merchant_agent_runtime import MerchantAgent

from .warehouse_copilot import CopilotAgent


class OwnedAgent:
    def __init__(self, agent, *, advisor=False):
        self.agent = agent
        self.advisor = advisor

    def stream_turn(self, messages, context, state):
        # This deployment serves the China travel market. Supply the clock through
        # the runtime's dynamic context, without changing its cached static prompt.
        if context.now is None and context.timezone is None:
            context = context.model_copy(update={"timezone": "Asia/Shanghai"})
        stream = self.agent.stream_turn(messages, context, state)
        return self._advisor_stream(stream, messages) if self.advisor else stream

    async def _advisor_stream(self, stream, messages):
        # Cards and tool progress still stream. Route-search text is rendered from
        # the server's result facts; ungrounded model prose never enters replay.
        start = len(messages)
        pending, summary = [], None
        async for event in stream:
            if event.type == "text_delta":
                pending.append(event)
                continue
            if event.type == "ui" and event.data.get("component") == "warehouse_routes":
                summary = event.data["payload"].get("summary")
            if event.type == "turn_complete":
                if summary is not None:
                    retained = []
                    for message in messages[start:]:
                        if message.get("role") == "assistant":
                            content = message.get("content", [])
                            content = (
                                [b for b in content if b.get("type") != "text"]
                                if isinstance(content, list)
                                else []
                            )
                            if not content:
                                continue
                            message = {**message, "content": content}
                        retained.append(message)
                    messages[start:] = retained + [
                        {"role": "assistant", "content": [{"type": "text", "text": summary}]}
                    ]
                    yield AgentEvent.text_delta(summary)
                else:
                    for text_event in pending:
                        yield text_event
            yield event

    async def aclose(self):
        await self.agent.client.close()


def factory(runtime, connectors):
    def build(role, actor, buyer_context):
        settings = {"model": os.environ["TOUR_MODEL"]} if os.environ.get("TOUR_MODEL") else {}
        if role == "advisor":
            return CopilotAgent(WarehouseAdvisorBackend(runtime, actor, connectors))
        else:
            agent = MerchantAgent(
                backend=WarehouseMerchantBackend(runtime, actor, buyer_org_id=buyer_context),
                config=MerchantAgentConfig(
                    **settings,
                    enable_memory=False,
                    enable_analysis=False,
                    enable_campaigns=False,
                    require_host_approval=True,
                    enable_web_search=False,
                    assistant_name="旅游云仓供应商助手",
                    brand_voice="使用简洁中文。未知价格、库存、销售额和订单数必须说未知。只使用当前供应组织的数据。报价上下文由工作台所选采购组织决定，不能根据姓名猜客户。变更必须先预览，再由供应商管理员在审批中心确认。不能把提议当作已执行。API 来源价格、库存和线路事实由上游维护；线路文字修改只形成云仓展示补充，不能说已修改上游。一期不创建订单、不占位、不收款。",
                ),
            )
        return OwnedAgent(agent, advisor=role == "advisor")

    return build
