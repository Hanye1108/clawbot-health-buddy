"""机器人核心：处理用户消息、主动推送。与具体的微信通道无关。"""
import datetime
import json
import logging
import random

from . import __version__, config, llm, records
from .db import kv_get, kv_set, now, now_iso
from .prompts import (HELP, MEAL_PROMPTS, MORNING_INSTR, QUIET_NOTICE, SYSTEM_PROMPT, WATER,
                      WEEKLY_INSTR, WEIGH, WELCOME)
from .tools import TOOLS, run_tool
from .weather import get_weather

log = logging.getLogger(__name__)


class Bot:
    def __init__(self, channel=None):
        self.channel = channel
        self.started = now()
        self.fails = {"llm": 0, "vision": 0}

    # ---------- 给管理员的告警 ----------
    def admin_uid(self):
        return self.channel.admin_uid() if self.channel else None

    def alert(self, key, text, cooldown_hours=6, keep=True):
        """出问题时通知管理员（同一类问题在冷却时间内只发一次）。发不出去就暂存，等管理员下次说话时补发。
        告警里不包含任何人的聊天内容。"""
        log.warning("告警[%s]：%s", key, text)
        last = kv_get(f"alert:{key}")
        if last and now() - datetime.datetime.fromisoformat(last) < datetime.timedelta(hours=cooldown_hours):
            return False
        kv_set(f"alert:{key}", now_iso())
        uid = self.admin_uid()
        if not uid:
            return False
        msg = "⚠️ 小助手运行提醒\n" + text
        if self.send(uid, msg):
            return True
        if keep:
            records.add_pending(uid, msg, 24 * 7)
        return False

    def _ok(self, kind):
        self.fails[kind] = 0

    def _failed(self, kind, err, threshold, what):
        self.fails[kind] += 1
        if self.fails[kind] >= threshold:
            self.alert(kind, f"{what}连续失败 {self.fails[kind]} 次，最近一次：{type(err).__name__}: {str(err)[:150]}\n"
                             "常见原因：DeepSeek 余额不足、Key 失效、模型名变化。发「#status」可查看余额。")

    def status_text(self):
        up = now() - self.started
        lines = [f"版本 {__version__}，已运行 {up.days} 天 {up.seconds // 3600} 小时，通道 {config.CHANNEL}"]
        lines += self.channel.status_lines()
        try:
            bal = llm.get_balance()
            lines.append(f"DeepSeek 余额：{bal['total']} {bal['currency']}" + ("" if bal["available"] else "（不可用！）"))
        except Exception as e:  # noqa: BLE001
            lines.append(f"DeepSeek 余额：查询失败（{type(e).__name__}）")
        lines.append(f"最近连续失败：对话 {self.fails['llm']} 次，看图 {self.fails['vision']} 次")
        return "\n".join(lines)

    # ---------- 发送 ----------
    def send(self, uid, text):
        try:
            return bool(self.channel.send_text(uid, text))
        except Exception:  # noqa: BLE001
            log.exception("发送失败")
            return False

    def with_quiet_notice(self, uid, text):
        """如果这是推送窗口关闭前最后一条能送达的提醒，附上一句说明（每次沉默只附一次）。"""
        deadline = self.channel.push_deadline(uid)
        if not deadline or now() > deadline:
            return text
        nxt = next_daily_push(now())
        key = f"quiet_notice:{uid}"
        if nxt and nxt > deadline and kv_get(key) != deadline.isoformat():
            kv_set(key, deadline.isoformat())
            return text + QUIET_NOTICE
        return text

    def push(self, uid, text, nudge=None, keep_hours=12):
        """主动推送。先正常发送；失败时：
        - 有 nudge：全文暂存，等她下次说话时补发；若通道支持兜底（测试号模板消息），再发一句提示。
        - 没有 nudge：尝试用兜底方式发全文（内容会被截短）。"""
        text = self.with_quiet_notice(uid, text)
        if self.send(uid, text):
            records.add_chat(uid, "assistant", text)
            return True
        if nudge:
            records.add_pending(uid, text, keep_hours)
            return self.channel.send_fallback(uid, nudge)
        if self.channel.send_fallback(uid, text):
            records.add_chat(uid, "assistant", text)
            return True
        log.warning("主动推送失败（用户 %s）", uid[-6:])
        return False

    def deliver_pending(self, uid):
        for text in records.take_pending(uid):
            if self.send(uid, text):
                records.add_chat(uid, "assistant", text)

    def welcome(self, uid):
        if self.send(uid, WELCOME):
            records.add_chat(uid, "assistant", WELCOME)

    # ---------- 接收 ----------
    def build_system(self, uid):
        p = records.get_profile(uid)
        info = {k: v for k, v in p.items() if k not in ("user_id", "updated_at") and v is not None}
        info_txt = json.dumps(info, ensure_ascii=False) if info else "（还没有资料）"
        return f"{SYSTEM_PROMPT}\n\n现在是北京时间{llm.now_text()}。\n已保存的用户资料：{info_txt}"

    def handle_command(self, uid, cmd):
        """内置指令，返回 True 表示已处理。"""
        replies = {
            "关闭称重提醒": ("weigh", False, "好的，以后不会再提醒你称体重了。想记的时候直接告诉我就行～"),
            "开启称重提醒": ("weigh", True, "好的，每周日早上会轻轻提醒一下～"),
            "关闭提醒": ("all", False, "好的，不会再主动打扰你啦。想恢复的话发「开启提醒」就行，"
                                    "平时直接找我聊天、记录饮食也都可以。"),
            "开启提醒": ("all", True, "好的，提醒已经开启～"),
        }
        if cmd == "清除数据":
            records.clear_user_data(uid)
            self.send(uid, "好的，你的资料、饮食、运动、体重记录和聊天记录都已经删除了，提醒也先关掉了。"
                           "想重新开始的话，发「开启提醒」就行。")
            return True
        if cmd in replies:
            kind, enabled, text = replies[cmd]
            records.set_reminders_flag(uid, enabled, kind)
            self.send(uid, text)
            return True
        if cmd in ("帮助", "help", "/help"):
            self.send(uid, HELP)
            return True
        is_admin = uid == self.admin_uid()
        if cmd == "#status" and is_admin:
            self.send(uid, self.status_text())
            return True
        if cmd == "#test alert" and is_admin:
            kv_set("alert:test", "")
            self.alert("test", "这是一条测试告警，能看到说明告警通道正常。", cooldown_hours=0)
            return True
        if cmd.startswith("#test"):
            parts = cmd.split()
            name = parts[1] if len(parts) > 1 else ""
            jobs = {
                "morning": lambda: morning_job(self, uid),
                "breakfast": lambda: meal_job(self, "breakfast", uid),
                "lunch": lambda: meal_job(self, "lunch", uid),
                "dinner": lambda: meal_job(self, "dinner", uid),
                "water": lambda: water_job(self, uid),
                "weigh": lambda: weigh_job(self, uid),
                "weekly": lambda: weekly_job(self, uid),
            }
            if name in jobs:
                jobs[name]()
            else:
                self.send(uid, "可测试：" + " ".join(jobs))
            return True
        return False

    def handle_text(self, uid, text):
        is_new = not records.get_profile(uid)
        records.ensure_user(uid)
        if is_new:
            self.welcome(uid)
        if self.handle_command(uid, text.strip()):
            return
        self.deliver_pending(uid)
        try:
            records.add_chat(uid, "user", text)
            messages = [{"role": "system", "content": self.build_system(uid)}] + records.load_history(uid)
            answer = llm.chat(messages, TOOLS, lambda name, args: run_tool(uid, name, args))
            records.add_chat(uid, "assistant", answer)
            self._ok("llm")
        except Exception as e:  # noqa: BLE001
            log.exception("处理消息失败")
            answer = "我刚刚走神了，能再说一遍吗？"
            self._failed("llm", e, 3, "DeepSeek 对话")
        self.send(uid, answer)

    def handle_image(self, uid, data, mime=None):
        try:
            result = llm.analyze_photo(data, mime)
            self._ok("vision")
        except Exception as e:  # noqa: BLE001
            log.exception("图片识别失败")
            self.send(uid, "这张照片我没看清楚，能用文字告诉我吃了什么吗？")
            self._failed("vision", e, 2, f"看图（模型 {config.VISION_MODEL}）")
            return
        text = "【我发来一张照片，以下是图片识别的估算结果，可能不准】" + json.dumps(result, ensure_ascii=False)
        self.handle_text(uid, text)


# ---------- 定时任务 ----------
def targets(only=None):
    if only:
        return [records.get_profile(only) or {"user_id": only}]
    from .db import run
    return [dict(r) for r in run("SELECT * FROM profiles WHERE reminders=1", fetch=True)]


def morning_job(bot, only=None):
    for p in targets(only):
        uid = p["user_id"]
        try:
            if not p.get("city"):
                if only:
                    bot.push(uid, "还不知道你在哪个城市，告诉我就能每天早上给你发天气啦～")
                continue
            weather = get_weather(p["city"], p.get("province") or "")
            if weather.startswith(("天气接口出错", "没有找到城市")):
                bot.alert("weather", f"早安天气查询失败：{weather[:150]}\n检查和风天气的 Key、API Host 和额度。", 24)
                continue
            text = llm.write(MORNING_INSTR, weather)
            bot.push(uid, text, nudge="早上好～今天的天气和提醒准备好啦，回我任意一句就能看到", keep_hours=5)
        except Exception as e:  # noqa: BLE001
            log.exception("早安推送失败")
            bot.alert("morning", f"早安推送出错：{type(e).__name__}: {str(e)[:150]}", 24)


def meal_job(bot, meal, only=None):
    day = now().strftime("%Y-%m-%d")
    for p in targets(only):
        uid = p["user_id"]
        if not only and records.has_meal(uid, day, meal):
            continue
        try:
            bot.push(uid, random.choice(MEAL_PROMPTS[meal]))
        except Exception:  # noqa: BLE001
            log.exception("饮食询问推送失败")


def water_job(bot, only=None):
    for p in targets(only):
        try:
            bot.push(p["user_id"], random.choice(WATER))
        except Exception:  # noqa: BLE001
            log.exception("喝水提醒推送失败")


def weigh_job(bot, only=None):
    since = (now().date() - datetime.timedelta(days=5)).isoformat()
    from .db import run
    for p in targets(only):
        uid = p["user_id"]
        if not only:
            if not p.get("weigh_reminder", 1):
                continue
            if run("SELECT 1 FROM weights WHERE user_id=? AND day>=? LIMIT 1", (uid, since), fetch=True):
                continue
        try:
            bot.push(uid, random.choice(WEIGH))
        except Exception:  # noqa: BLE001
            log.exception("称重提醒推送失败")


def weekly_job(bot, only=None):
    for p in targets(only):
        uid = p["user_id"]
        try:
            stats = records.week_stats(uid)
            if not stats["有记录的天数"] and not stats["运动天数"]:
                if only:
                    bot.push(uid, "这周还没有饮食或运动记录哦～先随手记一顿试试，有了记录我就能给你写小结啦。")
                continue
            text = llm.write(WEEKLY_INSTR, json.dumps(stats, ensure_ascii=False))
            bot.push(uid, text, nudge="本周的小结写好啦，回我任意一句就能看到～", keep_hours=24 * 7)
        except Exception:  # noqa: BLE001
            log.exception("周总结推送失败")


# 时间表（北京时间）。想改时间就改这里。
SCHEDULE = [
    (morning_job, {"hour": 7, "minute": 0}, ()),
    (weigh_job, {"day_of_week": "sun", "hour": 7, "minute": 30}, ()),
    (meal_job, {"hour": 8, "minute": 30}, ("breakfast",)),
    (water_job, {"hour": 10, "minute": 30}, ()),
    (meal_job, {"hour": 12, "minute": 30}, ("lunch",)),
    (water_job, {"hour": 15, "minute": 30}, ()),
    (meal_job, {"hour": 19, "minute": 0}, ("dinner",)),
    (weekly_job, {"day_of_week": "sun", "hour": 20, "minute": 30}, ()),
]


def balance_job(bot):
    """每天检查一次 DeepSeek 余额，不够了提前告诉管理员。"""
    try:
        bal = llm.get_balance()
    except Exception as e:  # noqa: BLE001
        log.warning("余额查询失败：%s", e)
        return
    try:
        low = float(bal["total"]) < config.ALERT_BALANCE
    except (TypeError, ValueError):
        low = False
    if not bal["available"] or low:
        bot.alert("balance", f"DeepSeek 余额只剩 {bal['total']} {bal['currency']}，"
                             "用完后机器人就不能回复了，记得去 platform.deepseek.com 充值。", 24)


# 只发给管理员的检查任务（不算在用户提醒里）
ADMIN_SCHEDULE = [
    (balance_job, {"hour": 9, "minute": 5}, ()),
]


def next_daily_push(after):
    """SCHEDULE 里每天都有的推送中，after 之后的下一次时间。"""
    slots = sorted({(w["hour"], w["minute"]) for _, w, _ in SCHEDULE if "day_of_week" not in w})
    for day in (0, 1):
        base = (after + datetime.timedelta(days=day)).replace(second=0, microsecond=0)
        for h, m in slots:
            t = base.replace(hour=h, minute=m)
            if t > after + datetime.timedelta(minutes=1):
                return t
    return None


def start_scheduler(bot):
    from apscheduler.schedulers.background import BackgroundScheduler

    s = BackgroundScheduler(timezone=str(config.TZ))
    for func, when, args in SCHEDULE + ADMIN_SCHEDULE:
        s.add_job(func, "cron", args=(bot, *args), misfire_grace_time=300, coalesce=True, **when)
    s.start()
    return s
