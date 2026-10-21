"""提供给大模型调用的工具（Function Calling）。"""
import json
import logging

from . import onboarding, records, reminders, timers
from .onboarding import STEP_NAMES
from .records import MET, PREF_KINDS
from .reminders import KINDS
from .search import web_search
from .weather import get_weather

log = logging.getLogger(__name__)

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "查询中国城市或区县的实时天气和未来几天预报。城市名可能重名时请同时传入省份。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "城市或区县名，如 杭州、长安区"},
            "province": {"type": "string", "description": "所在省份，如 陕西，可选"}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "save_profile",
        "description": "保存或更新用户资料。只传用户这次明确提供的字段。体重和身高用公斤和厘米。",
        "parameters": {"type": "object", "properties": {
            "nickname": {"type": "string", "description": "称呼"},
            "sex": {"type": "string", "enum": ["female", "male"]},
            "age": {"type": "integer"},
            "height_cm": {"type": "number"},
            "weight_kg": {"type": "number", "description": "当前体重，公斤"},
            "goal_weight_kg": {"type": "number", "description": "目标体重，公斤"},
            "activity": {"type": "string", "enum": ["sedentary", "light", "moderate", "active"]},
            "city": {"type": "string"},
            "province": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "calc_target",
        "description": "根据已保存的资料计算每日热量目标。需要性别、年龄、身高、体重、活动量齐全。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "log_meal",
        "description": "记录用户吃的一餐，热量为你按常见分量估算的中间值。",
        "parameters": {"type": "object", "properties": {
            "meal": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
            "description": {"type": "string", "description": "吃了什么，简短描述"},
            "kcal": {"type": "number", "description": "估算的总热量，大卡"},
            "date": {"type": "string", "description": "日期 YYYY-MM-DD，默认今天，补记以前的才需要"}},
            "required": ["meal", "description", "kcal"]}}},
    {"type": "function", "function": {
        "name": "delete_meal",
        "description": "按编号删除一条饮食记录（编号可从 get_today_log 得到）。",
        "parameters": {"type": "object", "properties": {
            "meal_id": {"type": "integer"}}, "required": ["meal_id"]}}},
    {"type": "function", "function": {
        "name": "log_exercise",
        "description": "记录一次运动。用户只说了运动和时长时，不要传 kcal，由程序估算；用户直接说了消耗（比如手表显示的）才传 kcal。",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": list(MET.keys()),
                     "description": "运动类型，选最接近的，实在没有就用 other"},
            "description": {"type": "string", "description": "简短描述，如 夜跑、健身房练腿"},
            "minutes": {"type": "number", "description": "运动时长，分钟"},
            "kcal": {"type": "number", "description": "用户自己报的消耗大卡，可选"},
            "date": {"type": "string", "description": "日期 YYYY-MM-DD，默认今天"}},
            "required": ["kind"]}}},
    {"type": "function", "function": {
        "name": "delete_exercise",
        "description": "按编号删除一条运动记录（编号可从 get_today_log 得到）。",
        "parameters": {"type": "object", "properties": {
            "exercise_id": {"type": "integer"}}, "required": ["exercise_id"]}}},
    {"type": "function", "function": {
        "name": "log_weight",
        "description": "记录体重（公斤），并返回体重趋势和按新体重计算的每日建议摄入。",
        "parameters": {"type": "object", "properties": {
            "kg": {"type": "number", "description": "体重，公斤（斤要先除以2）"},
            "date": {"type": "string", "description": "日期 YYYY-MM-DD，默认今天"}},
            "required": ["kg"]}}},
    {"type": "function", "function": {
        "name": "get_today_log",
        "description": "查看今天已记录的饮食和运动、合计热量和剩余额度。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_week_summary",
        "description": "获取最近7天的饮食、运动和体重统计，用来写周总结。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_reminder",
        "description": "修改提醒设置：开关、时间、哪几天。kind=all 是全部提醒的总开关（只能设 enabled）。只传要改的字段。",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["all"] + list(KINDS.keys())},
            "enabled": {"type": "boolean"},
            "times": {"type": "string", "description": "HH:MM，多个用逗号分开，如 09:00 或 10:00,15:00,17:30"},
            "days": {"type": "string", "enum": ["all", "weekdays", "weekends",
                                                 "mon", "tue", "wed", "thu", "fri", "sat", "sun"]}},
            "required": ["kind"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "联网搜索实时信息：新闻、最近发生的事、价格、营业时间、某个食物/产品的最新资料等。闲聊和你本来就知道的常识不要搜。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词，简短（不超过128字），可用 after:2026-10-01 这样的语法限定时间"},
            "question": {"type": "string", "description": "用户的原话，用来给结果排序"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "set_timer",
        "description": "设置一条临时提醒（比如「20秒后提醒我喝水」「下午3点提醒我开会」「每天晚上9点提醒我吃维生素」）。"
                       "相对时间用 delay_seconds，具体时刻用 at；到点会发一条「⏰ 内容」给用户。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "提醒内容，简短，比如「喝水」「吃维生素」「去开会」"},
            "delay_seconds": {"type": "number", "description": "多少秒后提醒（20分钟=1200）"},
            "at": {"type": "string", "description": "具体时刻，YYYY-MM-DD HH:MM，或只写 HH:MM（已过则默认明天）"},
            "repeat": {"type": "string", "enum": ["none", "daily", "weekdays", "weekends"],
                       "description": "重复：none一次（默认）、daily每天、weekdays工作日、weekends周末"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "list_timers",
        "description": "查看用户设置的、还没发的临时提醒。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "cancel_timer",
        "description": "取消临时提醒。编号从 list_timers 得到；all=true 取消全部。",
        "parameters": {"type": "object", "properties": {
            "timer_ids": {"type": "array", "items": {"type": "integer"}},
            "all": {"type": "boolean"}}}}},
    {"type": "function", "function": {
        "name": "get_reminders",
        "description": "查看用户当前的全部提醒设置。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "update_preferences",
        "description": "记录或删除用户的口味与忌口。",
        "parameters": {"type": "object", "properties": {
            "add": {"type": "object", "description": "要新增的条目，键为类别：喜欢、不吃、过敏或不耐受、饮食习惯；值为字符串数组",
                    "properties": {k: {"type": "array", "items": {"type": "string"}} for k in PREF_KINDS}},
            "remove": {"type": "array", "items": {"type": "string"}, "description": "要删除的条目原文"}}}}},
    {"type": "function", "function": {
        "name": "log_water",
        "description": "记录一次喝水。",
        "parameters": {"type": "object", "properties": {
            "ml": {"type": "number", "description": "毫升，一杯按250，一瓶按500"},
            "date": {"type": "string", "description": "日期 YYYY-MM-DD，默认今天"}},
            "required": ["ml"]}}},
    {"type": "function", "function": {
        "name": "delete_water",
        "description": "按编号删除一条喝水记录（编号可从 get_today_log 得到）。",
        "parameters": {"type": "object", "properties": {
            "water_id": {"type": "integer"}}, "required": ["water_id"]}}},
    {"type": "function", "function": {
        "name": "get_month_summary",
        "description": "获取某个月（默认本月至今）的饮食、运动、体重、喝水统计，用来写月度小结。",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string", "description": "YYYY-MM，可选"}}}}},
    {"type": "function", "function": {
        "name": "skip_onboarding_step",
        "description": "新手引导中，用户不想回答某一项时调用，之后不再问这一项。",
        "parameters": {"type": "object", "properties": {
            "step": {"type": "string", "enum": STEP_NAMES}}, "required": ["step"]}}},
    {"type": "function", "function": {
        "name": "finish_onboarding",
        "description": "结束新手引导（资料问完了，或用户说以后再填）。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_report_link",
        "description": "生成一个临时网页链接，可以看体重、热量、运动、喝水的趋势图，也可以下载全部记录（CSV表格）。",
        "parameters": {"type": "object", "properties": {}}}},
]


def run_tool(uid, name, args):
    try:
        if name == "get_weather":
            return get_weather(args.get("city", ""), args.get("province", ""))
        if name == "save_profile":
            return records.save_profile(uid, args)
        if name == "calc_target":
            return records.calc_target(uid)
        if name == "log_meal":
            return records.log_meal(uid, args)
        if name == "delete_meal":
            return records.delete_meal(uid, args)
        if name == "log_exercise":
            return records.log_exercise(uid, args)
        if name == "delete_exercise":
            return records.delete_exercise(uid, args)
        if name == "log_weight":
            return records.log_weight(uid, args)
        if name == "get_today_log":
            return json.dumps(records.day_summary(uid), ensure_ascii=False)
        if name == "get_week_summary":
            return json.dumps(records.week_stats(uid), ensure_ascii=False)
        if name == "set_reminder":
            return reminders.tool_set(uid, args)
        if name == "web_search":
            return web_search(args.get("query", ""), args.get("question", ""))
        if name == "set_timer":
            return timers.create(uid, args)
        if name == "list_timers":
            return timers.list_timers(uid)
        if name == "cancel_timer":
            return timers.cancel(uid, args)
        if name == "get_reminders":
            return reminders.describe(uid)
        if name == "update_preferences":
            return records.update_prefs(uid, args)
        if name == "log_water":
            return records.log_water(uid, args)
        if name == "delete_water":
            return records.delete_water(uid, args)
        if name == "get_month_summary":
            return json.dumps(records.month_stats(uid, args.get("month")), ensure_ascii=False)
        if name == "skip_onboarding_step":
            return onboarding.skip(uid, args.get("step", ""))
        if name == "finish_onboarding":
            return onboarding.finish(uid)
        if name == "get_report_link":
            from . import web
            return web.link_for_tool(uid)
    except Exception as e:  # noqa: BLE001 - 工具出错要告诉模型，而不是让整个回复失败
        log.exception("工具 %s 出错", name)
        return f"工具出错：{e}"
    return "未知工具"
