"""提供给大模型调用的工具（Function Calling）。"""
import json
import logging

from . import records
from .records import MET
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
        "name": "set_reminders",
        "description": "开启或关闭定时提醒。kind=all 表示全部提醒（天气、饮食询问、喝水、称重），kind=weigh 只针对每周称重提醒。",
        "parameters": {"type": "object", "properties": {
            "enabled": {"type": "boolean"},
            "kind": {"type": "string", "enum": ["all", "weigh"]}},
            "required": ["enabled"]}}},
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
        if name == "set_reminders":
            kind = args.get("kind") or "all"
            records.set_reminders_flag(uid, bool(args.get("enabled")), kind)
            what = "称重提醒" if kind == "weigh" else "提醒"
            return f"已开启{what}" if args.get("enabled") else f"已关闭{what}"
    except Exception as e:  # noqa: BLE001 - 工具出错要告诉模型，而不是让整个回复失败
        log.exception("工具 %s 出错", name)
        return f"工具出错：{e}"
    return "未知工具"
