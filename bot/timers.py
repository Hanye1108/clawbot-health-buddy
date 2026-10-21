"""临时提醒：「20分钟后提醒我喝水」「下午3点提醒我开会」「每天晚上9点提醒我吃维生素」。

- 存在数据库里，服务重启后会重新排上；错过的（比如重启期间到点的）会在 1 分钟内补发。
- 到点用精确的定时任务发送，所以「20秒后」也能准时。
- 仍然受微信约 24 小时推送窗口限制：她太久没说话，提醒会先存起来，等她下次说话时补发。
"""
import datetime
import logging
import re

from . import config
from .db import insert, now, now_iso, run, update

log = logging.getLogger(__name__)
MAX_PER_USER = 20
MAX_DAYS = 30
MIN_SECONDS = 10
REPEATS = {"none": "一次", "daily": "每天", "weekdays": "工作日", "weekends": "周末"}

_scheduler = None
_bot = None


# ---------- 时间计算 ----------
def _next_repeat(due, repeat):
    nxt = due + datetime.timedelta(days=1)
    while (repeat == "weekdays" and nxt.weekday() >= 5) or (repeat == "weekends" and nxt.weekday() < 5):
        nxt += datetime.timedelta(days=1)
    return nxt


def _parse_due(args):
    """返回 (到点时间, 错误信息)。支持 delay_seconds、或 at=「YYYY-MM-DD HH:MM」/「HH:MM」。"""
    t = now()
    if args.get("delay_seconds") not in (None, ""):
        try:
            sec = float(args["delay_seconds"])
        except (TypeError, ValueError):
            return None, "delay_seconds 需要是数字"
        if sec < MIN_SECONDS:
            return None, f"最短 {MIN_SECONDS} 秒"
        return t + datetime.timedelta(seconds=sec), None
    at = str(args.get("at") or "").strip()
    m = re.fullmatch(r"(?:(\d{4})-(\d{1,2})-(\d{1,2})[ T])?(\d{1,2})[:：](\d{2})", at)
    if not m:
        return None, "需要 delay_seconds，或 at（格式 YYYY-MM-DD HH:MM 或 HH:MM）"
    y, mo, d, h, mi = m.groups()
    try:
        if y:
            due = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=config.TZ)
        else:
            due = t.replace(hour=int(h), minute=int(mi), second=0, microsecond=0)
            if due <= t:   # 只说了时间且已经过了：默认明天
                due += datetime.timedelta(days=1)
    except ValueError:
        return None, "日期或时间不存在"
    if due <= t:
        return None, "这个时间已经过去了"
    return due, None


def _fmt(due):
    t = now()
    day = {0: "今天", 1: "明天", 2: "后天"}.get((due.date() - t.date()).days, due.strftime("%m月%d日"))
    return f"{day} {due.strftime('%H:%M:%S' if due.second else '%H:%M')}"


# ---------- 增删查 ----------
def create(uid, args):
    text = str(args.get("text") or "").strip()[:200]
    if not text:
        return "缺少提醒内容 text"
    repeat = args.get("repeat") or "none"
    if repeat not in REPEATS:
        return f"repeat 只能是：{', '.join(REPEATS)}"
    due, err = _parse_due(args)
    if err:
        return f"设置失败：{err}"
    if (repeat == "weekdays" and due.weekday() >= 5) or (repeat == "weekends" and due.weekday() < 5):
        due = _next_repeat(due, repeat)
    if due - now() > datetime.timedelta(days=MAX_DAYS):
        return f"设置失败：最多只能提前 {MAX_DAYS} 天"
    n = run("SELECT COUNT(*) AS n FROM timers WHERE user_id=? AND sent=0", (uid,), fetch=True)[0]["n"]
    if n >= MAX_PER_USER:
        return f"设置失败：最多同时 {MAX_PER_USER} 条提醒，先取消一些（list_timers 查看）"
    tid = insert("INSERT INTO timers (user_id, due_at, text, repeat, sent, created_at) VALUES (?,?,?,?,0,?)",
                 (uid, due.isoformat(), text, repeat, now_iso()))
    _schedule(tid, due)
    res = f"已设置（编号{tid}）：{_fmt(due)}提醒「{text}」" + ("" if repeat == "none" else f"，之后{REPEATS[repeat]}这个时间都提醒")
    deadline = _bot.channel.push_deadline(uid) if _bot and _bot.channel else None
    if deadline and due > deadline:
        res += ("。注意：到那时她可能已经超过一天没和你说话，微信不允许主动发消息，"
                "提醒会等她下次说话时补发。请顺带告诉她这一点")
    return res


def list_timers(uid):
    rows = run("SELECT id, due_at, text, repeat FROM timers WHERE user_id=? AND sent=0 ORDER BY due_at",
               (uid,), fetch=True)
    if not rows:
        return "没有待发的临时提醒"
    return "\n".join(f"编号{r['id']}：{_fmt(datetime.datetime.fromisoformat(r['due_at']))} "
                     f"「{r['text']}」{'' if r['repeat'] == 'none' else '（' + REPEATS[r['repeat']] + '）'}"
                     for r in rows)


def cancel(uid, args):
    ids = args.get("timer_ids") or ([args["timer_id"]] if args.get("timer_id") is not None else [])
    if args.get("all"):
        ids = [r["id"] for r in run("SELECT id FROM timers WHERE user_id=? AND sent=0", (uid,), fetch=True)]
    done = []
    for tid in ids:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        if update("UPDATE timers SET sent=2 WHERE id=? AND user_id=? AND sent=0", (tid, uid)):
            done.append(tid)
            _unschedule(tid)
    return f"已取消：{', '.join(map(str, done))}" if done else "没有找到可以取消的提醒"


# ---------- 到点发送 ----------
def fire(tid):
    rows = run("SELECT * FROM timers WHERE id=?", (tid,), fetch=True)
    if not rows or rows[0]["sent"] != 0:
        return
    r = rows[0]
    if r["repeat"] == "none":
        if not update("UPDATE timers SET sent=1 WHERE id=? AND sent=0", (tid,)):
            return   # 已被别处发出
    else:
        nxt = _next_repeat(datetime.datetime.fromisoformat(r["due_at"]), r["repeat"])
        while nxt <= now():
            nxt = _next_repeat(nxt, r["repeat"])
        if not update("UPDATE timers SET due_at=? WHERE id=? AND due_at=?", (nxt.isoformat(), tid, r["due_at"])):
            return
        _schedule(tid, nxt)
    if _bot:
        _bot.push(r["user_id"], f"⏰ {r['text']}", nudge="你有一条提醒，回我任意一句就能看到", keep_hours=24)


def fire_overdue():
    """兜底：每分钟检查一次，已经到点但还没发的（比如服务重启期间到点的）补发。"""
    cutoff = (now() - datetime.timedelta(seconds=30)).isoformat()
    for r in run("SELECT id FROM timers WHERE sent=0 AND due_at <= ?", (cutoff,), fetch=True):
        fire(r["id"])


def _schedule(tid, due):
    if _scheduler:
        _scheduler.add_job(fire, "date", run_date=due, args=(tid,), id=f"timer-{tid}",
                           replace_existing=True, misfire_grace_time=60)


def _unschedule(tid):
    if _scheduler:
        try:
            _scheduler.remove_job(f"timer-{tid}")
        except Exception:  # noqa: BLE001 - 任务可能已经执行完
            pass


def attach(scheduler, bot):
    global _scheduler, _bot
    _scheduler, _bot = scheduler, bot
    if scheduler is None:
        return
    rows = run("SELECT id, due_at FROM timers WHERE sent=0", fetch=True)
    for r in rows:
        due = datetime.datetime.fromisoformat(r["due_at"])
        if due > now():
            _schedule(r["id"], due)
    log.info("已加载 %d 条临时提醒", len(rows))
