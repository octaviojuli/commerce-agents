"""Finite advisor actions derived from saved business state, never model intent."""

from . import trip_brief

LABELS = {
    "ask_clarify": "补充一个需求",
    "present_directions": "看看三个方向",
    "note_memory": "记住客人的顾虑",
    "search_routes": "按需求找线",
    "lookup_route": "按名称查线路",
    "answer_question": "继续问线路细节",
    "accept_changes": "核对需求变化",
    "compare_routes": "比较两三条线路",
    "route_facts": "查看行程依据",
    "build_plan": "整理推荐方案",
    "list_departures": "查看团期",
    "compare_dates": "比较出发日期",
    "check_seats": "核对可售状态",
    "build_confirmation": "核对确认单",
    "record_confirmation": "记录客人确认",
    "add_note": "添加行程备注",
    "quote": "生成正式报价",
    "share_quote": "分享报价",
    "recheck_price": "重新核价",
    "collect_documents": "整理旅客材料",
    "predeparture_task": "查看行前待办",
}


def derive(brief, *, visible=(), pending=False, confirmed=False, sold=False):
    ready = trip_brief.readiness(brief)
    if sold:
        stage, actions = "won", ["collect_documents", "predeparture_task", "answer_question"]
    elif pending:
        stage, actions = "need", ["accept_changes", "ask_clarify", "note_memory"]
    elif not ready["search"]["ready"]:
        stage, actions = (
            "explore",
            ["ask_clarify", "present_directions", "lookup_route", "note_memory"],
        )
        if visible:
            actions += ["list_departures", "route_facts", "answer_question", "compare_routes"]
    elif brief.departure_id:
        stage = "quote" if confirmed else "confirm"
        actions = (
            ["quote", "share_quote", "recheck_price"]
            if confirmed
            else ["build_confirmation", "record_confirmation", "add_note", "ask_clarify"]
        )
        if ready["quote"]["ready"] and "recheck_price" not in actions:
            actions.append("recheck_price")
        actions += ["answer_question", "route_facts", "list_departures"]
    elif brief.route_id:
        stage, actions = (
            "date",
            [
                "list_departures",
                "compare_dates",
                "check_seats",
                "answer_question",
                "route_facts",
                "ask_clarify",
            ],
        )
    elif visible:
        stage, actions = (
            "select",
            [
                "compare_routes",
                "route_facts",
                "build_plan",
                "answer_question",
                "list_departures",
                "search_routes",
                "ask_clarify",
            ],
        )
    else:
        stage, actions = "need", ["search_routes", "ask_clarify", "lookup_route", "note_memory"]
    return {"stage": stage, "allowed_actions": actions}


def chips(actions):
    return [{"label": LABELS[a], "action": a} for a in actions[:3]]
