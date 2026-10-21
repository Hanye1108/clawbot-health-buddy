"""每个人一套提醒设置：开关、时间、哪几天。

调度方式：每分钟检查一次，到点（5 分钟内）且还没发过就发，所以服务刚好在整点重启也不会漏。
"""
import datetime
import json
import random
import re

from .db import now, run

# 默认设置。times 可以有多个（用逗号分开），days 见 DAYS。
KINDS = {
    "morning":   {"name": "早安天气", "times": "07:00", "days": "all", "enabled": 1},
    "breakfast": {"name": "早餐询问", "times": "08:30", "days": "all", "enabled": 1},
    "lunch":     {"name": "午餐询问", "times": "12:30", "days": "all", "enabled": 1},
    "dinner":    {"name": "晚餐询问", "times": "19:00", "days": "all", "enabled": 1},
    "water":     {"name": "喝水提醒", "times": "10:30,15:30", "days": "all", "enabled": 1},
    "night":     {"name": "睡前小结", "times": "21:30", "days": "all", "enabled": 0},
    "weigh":     {"name": "称重提醒", "times": "07:30", "days": "sun", "enabled": 1},
    "weekly":    {"name": "周总结", "times": "20:30", "days": "sun", "enabled": 1},
    "monthly":   {"name": "月度小结", "times": "20:00", "days": "month1", "enabled": 1},
    # 随机关心：times 是「时间段x每天几次」，每天在时间段里随机挑时间
    "care":      {"name": "随机关心", "times": "10:00-20:00x1", "days": "all", "enabled": 1},
}
CARE_GAP_MINUTES = 90        # 同一天两次随机关心至少间隔
CARE_AVOID_MINUTES = 20      # 离其他提醒至少这么远
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAYS = {"all": "每天", "weekdays": "工作日", "weekends": "周末", "month1": "每月1号",
        "mon": "每周一", "tue": "每周二", "wed": "每周三", "thu": "每周四",
        "fri": "每周五", "sat": "每周六", "sun": "每周日"}
WEEKLY_KINDS = ("weigh", "weekly")
GRACE_MINUTES = 5


def _parse_times(value):
    times = []
    for part in re.split(r"[,，、\s]+", str(value or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", part)
        if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
            raise ValueError(f"时间格式不对：{part}，应为 HH:MM")
        times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    if not times or len(times) > 6:
        raise ValueError("需要 1～6 个时间")
    return sorted(set(times))


def _parse_window(value, count=None):
    """「10:00-20:00」+ 次数 → 「10:00-20:00x1」"""
    m = re.fullmatch(r"\s*(\d{1,2})[:：](\d{2})\s*[-~～到至]\s*(\d{1,2})[:：](\d{2})\s*(?:x(\d))?\s*", str(value or ""))
    if not m:
        raise ValueError("随机关心的时间要写成时间段，比如 10:00-20:00")
    h1, m1, h2, m2 = (int(x) for x in m.groups()[:4])
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
        raise ValueError("时间不对")
    n = int(count if count is not None else (m.group(5) or 1))
    if not 1 <= n <= 3:
        raise ValueError("每天 1～3 次")
    span = (h2 * 60 + m2) - (h1 * 60 + m1)
    if span < 60 * n:
        raise ValueError(f"时间段太短，每天 {n} 次至少需要 {n} 小时")
    return f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}x{n}"


def care_window(raw):
    """「10:00-20:00x1」→ (开始分钟, 结束分钟, 次数)"""
    win, _, n = raw.partition("x")
    a, b = win.split("-")
    to_min = lambda t: int(t[:2]) * 60 + int(t[3:])   # noqa: E731
    return to_min(a), to_min(b), int(n or 1)


def care_slots(uid, d, settings=None):
    """某天随机关心的时间（同一个人同一天结果固定，重启不会变）。"""
    settings = settings or get_settings(uid)
    c = settings["care"]
    start, end, n = care_window(c["times"][0])
    busy = []
    for kind, s in settings.items():
        if kind == "care" or not s["enabled"] or not day_matches(s["days"], d):
            continue
        busy += [int(t[:2]) * 60 + int(t[3:]) for t in s["times"]]
    rnd = random.Random(f"{uid}|{d.isoformat()}")
    picked = []
    for _ in range(300):
        if len(picked) == n:
            break
        t = rnd.randint(start, end)
        if any(abs(t - b) < CARE_AVOID_MINUTES for b in busy):
            continue
        if any(abs(t - p) < CARE_GAP_MINUTES for p in picked):
            continue
        picked.append(t)
    return [f"{t // 60:02d}:{t % 60:02d}" for t in sorted(picked)]


def times_on(uid, kind, c, d, settings):
    if kind == "care":
        return care_slots(uid, d, settings)
    return c["times"]


def get_settings(uid):
    rows = {r["kind"]: r for r in run("SELECT * FROM reminder_settings WHERE user_id=?", (uid,), fetch=True)}
    out = {}
    for kind, d in KINDS.items():
        r = rows.get(kind)
        out[kind] = {
            "name": d["name"],
            "enabled": bool(r["enabled"] if r and r["enabled"] is not None else d["enabled"]),
            "times": (r["times"] if r and r["times"] else d["times"]).split(","),
            "days": r["days"] if r and r["days"] else d["days"],
        }
    return out


def set_setting(uid, kind, enabled=None, times=None, days=None, count=None):
    if kind not in KINDS:
        raise ValueError(f"没有这种提醒：{kind}")
    cur = get_settings(uid)[kind]
    if kind == "care":
        if times is not None or count is not None:
            win = times if times is not None else cur["times"][0].partition("x")[0]
            times = _parse_window(win, count if count is not None else
                                  (None if times is not None and "x" in str(times) else care_window(cur["times"][0])[2]))
    elif times is not None:
        times = ",".join(_parse_times(times))
    if days is not None:
        allowed = WEEKDAY_KEYS if kind in WEEKLY_KINDS else (("month1",) if kind == "monthly"
                                                             else ("all", "weekdays", "weekends"))
        if days not in allowed:
            raise ValueError(f"{KINDS[kind]['name']} 的 days 只能是：{', '.join(allowed)}")
    run("INSERT INTO reminder_settings (user_id, kind, enabled, times, days) VALUES (?,?,?,?,?) "
        "ON CONFLICT(user_id, kind) DO UPDATE SET enabled=excluded.enabled, times=excluded.times, "
        "days=excluded.days",
        (uid, kind, int(cur["enabled"] if enabled is None else bool(enabled)),
         times or ",".join(cur["times"]), days or cur["days"]))


def describe(uid):
    from .records import get_profile
    master = get_profile(uid).get("reminders", 1)
    s = get_settings(uid)
    lines = [] if master else ["（总开关已关闭，下面的提醒暂时都不会发；发「开启提醒」恢复）"]
    for kind, c in s.items():
        state = "开" if c["enabled"] else "关"
        if kind == "care":
            a, b, n = care_window(c["times"][0])
            lines.append(f"{c['name']}：{state}，{DAYS.get(c['days'], c['days'])} {a // 60:02d}:{a % 60:02d}"
                         f"-{b // 60:02d}:{b % 60:02d} 之间随机 {n} 次")
            continue
        lines.append(f"{c['name']}：{state}，{DAYS.get(c['days'], c['days'])} {'、'.join(c['times'])}")
    return "\n".join(lines)


def day_matches(days, d):
    wd = d.weekday()
    if days == "all":
        return True
    if days == "weekdays":
        return wd < 5
    if days == "weekends":
        return wd >= 5
    if days == "month1":
        return d.day == 1
    return days in WEEKDAY_KEYS and WEEKDAY_KEYS.index(days) == wd


def due(uid, at=None):
    """返回此刻该发、还没发过的 (kind, slot) 列表，并记为已发。"""
    at = at or now()
    out = []
    settings = get_settings(uid)
    for kind, c in settings.items():
        if not c["enabled"]:
            continue
        for t in times_on(uid, kind, c, at.date(), settings):
            h, m = map(int, t.split(":"))
            sched = at.replace(hour=h, minute=m, second=0, microsecond=0)
            if not (0 <= (at - sched).total_seconds() < GRACE_MINUTES * 60):
                continue
            if not day_matches(c["days"], sched.date()):
                continue
            slot = sched.strftime("%Y-%m-%d %H:%M")
            if run("SELECT 1 FROM reminder_log WHERE user_id=? AND kind=? AND slot=?", (uid, kind, slot), fetch=True):
                continue
            run("INSERT INTO reminder_log (user_id, kind, slot) VALUES (?,?,?)", (uid, kind, slot))
            out.append((kind, slot))
    return out


def cleanup_log():
    cutoff = (now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    run("DELETE FROM reminder_log WHERE slot < ?", (cutoff,))


def next_push_time(uid, after):
    """这个人下一次定时推送的时间（只看每天类的提醒，用来判断推送窗口）。"""
    cands = []
    settings = get_settings(uid)
    for kind, c in settings.items():
        if not c["enabled"] or c["days"] in WEEKDAY_KEYS or c["days"] == "month1":
            continue
        for day in (0, 1, 2):
            base = (after + datetime.timedelta(days=day)).replace(second=0, microsecond=0)
            if not day_matches(c["days"], base.date()):
                continue
            for t in times_on(uid, kind, c, base.date(), settings):
                h, m = map(int, t.split(":"))
                cand = base.replace(hour=h, minute=m)
                if cand > after + datetime.timedelta(minutes=1):
                    cands.append(cand)
    return min(cands) if cands else None


def tool_set(uid, args):
    """给模型用的工具。"""
    from .records import set_reminders_flag
    kind = args.get("kind") or "all"
    try:
        if kind == "all":
            if args.get("enabled") is None:
                return "kind=all 时只能设置 enabled（总开关）"
            set_reminders_flag(uid, bool(args["enabled"]))
        else:
            set_setting(uid, kind, enabled=args.get("enabled"), times=args.get("times"), days=args.get("days"),
                        count=args.get("count"))
    except ValueError as e:
        return f"设置失败：{e}"
    return json.dumps({"状态": "已更新", "当前设置": describe(uid)}, ensure_ascii=False)
