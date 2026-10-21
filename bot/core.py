"""机器人核心：处理用户消息、主动推送。与具体的微信通道无关。"""
import datetime
import json
import logging
import random
import re

from . import __version__, config, llm, onboarding, records, reminders
from .db import kv_get, kv_set, now, now_iso, run
from .prompts import (ADMIN_GUIDE, FEATURE_GUIDE, INTRO, INTRO_ASK_NAME, INTRO_VERSION, MEAL_PROMPTS,
                      MONTHLY_INSTR, MORNING_INSTR, NIGHT_INSTR, QUIET_NOTICE, SYSTEM_PROMPT, WATER,
                      WEEKLY_INSTR, WEIGH, WHATS_NEW)
from .tools import TOOLS, run_tool
from .weather import get_weather

log = logging.getLogger(__name__)
GREETING = re.compile(r"^\s*(你好|您好|hi|hello|嗨|哈喽|在吗|在不在|hey|开始|嘿)[呀啊吖~～!！。.\s]*$", re.I)
GUIDE_CMDS = ("功能介绍", "帮助", "help", "/help", "你能做什么", "使用说明", "怎么用")


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
        lines.append("趋势图网页：" + (config.PUBLIC_URL or "未配置 PUBLIC_URL"))
        return "\n".join(lines)

    # ---------- 发送 ----------
    def send(self, uid, text):
        try:
            return bool(self.channel.send_text(uid, text))
        except Exception:  # noqa: BLE001
            log.exception("发送失败")
            return False

    def say(self, uid, text):
        """发送并记入聊天历史（让模型知道自己说过什么）。"""
        if self.send(uid, text):
            records.add_chat(uid, "assistant", text)
            return True
        return False

    def with_quiet_notice(self, uid, text):
        """如果这是推送窗口关闭前最后一条能送达的提醒，附上一句说明（每次沉默只附一次）。"""
        deadline = self.channel.push_deadline(uid)
        if not deadline or now() > deadline:
            return text
        nxt = reminders.next_push_time(uid, now())
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
        if self.say(uid, text):
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
            self.say(uid, text)

    # ---------- 自我介绍 ----------
    def introduce(self, uid, ask_name=True):
        for text in INTRO:
            self.say(uid, text)
        if uid == self.admin_uid():
            self.say(uid, ADMIN_GUIDE)
        onboarding.start(uid)
        if ask_name:
            self.say(uid, INTRO_ASK_NAME)
        kv_set(f"intro:{uid}", INTRO_VERSION)

    def welcome(self, uid):
        """测试号的「关注」事件：直接做自我介绍。"""
        records.ensure_user(uid)
        self.introduce(uid)

    def guide(self, uid):
        self.send(uid, FEATURE_GUIDE)
        if uid == self.admin_uid():
            self.send(uid, ADMIN_GUIDE)

    # ---------- 接收 ----------
    def build_system(self, uid):
        p = records.get_profile(uid)
        skip = ("user_id", "updated_at", "prefs", "weigh_reminder")
        info = {k: v for k, v in p.items() if k not in skip and v is not None}
        info["口味与忌口"] = records.get_prefs(uid)
        return (f"{SYSTEM_PROMPT}\n\n现在是北京时间{llm.now_text()}（{'一二三四五六日'[now().weekday()]}）。\n"
                f"已保存的用户资料：{json.dumps(info, ensure_ascii=False)}" + onboarding.prompt_for(uid))

    def handle_command(self, uid, cmd):
        """内置指令，返回 True 表示已处理。"""
        replies = {
            "关闭称重提醒": ("weigh", False, "好的，以后不会再提醒你称体重了。想记的时候直接告诉我就行～"),
            "开启称重提醒": ("weigh", True, "好的，每周日早上会轻轻提醒一下～"),
            "关闭提醒": ("all", False, "好的，不会再主动打扰你啦。想恢复的话发「开启提醒」就行，"
                                    "平时直接找我聊天、记录饮食也都可以。"),
            "开启提醒": ("all", True, "好的，提醒已经开启～ 发「我的提醒设置」可以看看都有哪些提醒。"),
        }
        if cmd == "清除数据":
            records.clear_user_data(uid)
            self.send(uid, "好的，你的资料、饮食、运动、体重、喝水记录、提醒设置和聊天记录都已经删除了，"
                           "提醒也先关掉了。想重新开始的话，发「重新填写资料」和「开启提醒」就行。")
            return True
        if cmd in replies:
            kind, enabled, text = replies[cmd]
            records.set_reminders_flag(uid, enabled, kind)
            self.send(uid, text)
            return True
        if cmd in GUIDE_CMDS:
            self.guide(uid)
            return True
        if cmd in ("我的提醒设置", "提醒设置", "查看提醒"):
            self.send(uid, "你现在的提醒：\n" + reminders.describe(uid) +
                      "\n\n想改就直接说，比如「早饭提醒改到9点」「周末不用提醒」「开启睡前小结」。")
            return True
        is_admin = uid == self.admin_uid()
        if cmd == "#status" and is_admin:
            self.send(uid, self.status_text())
            return True
        if cmd == "#test alert" and is_admin:
            kv_set("alert:test", "")
            self.alert("test", "这是一条测试告警，能看到说明告警通道正常。", cooldown_hours=0)
            return True
        if cmd == "#test intro":
            self.introduce(uid, ask_name=False)
            onboarding.finish(uid)
            return True
        if cmd in ("重新填写资料", "完善资料", "#test onboarding"):
            onboarding.start(uid)
            left = onboarding.remaining(uid)
            if left:
                self.handle_llm(uid, "我想完善一下我的资料")
            else:
                onboarding.finish(uid)
                self.send(uid, "你的基本资料都填好啦。想改哪项直接告诉我，比如「我现在55公斤」「我搬到杭州了」。")
            return True
        if cmd.startswith("#test"):
            parts = cmd.split()
            name = parts[1] if len(parts) > 1 else ""
            if name in reminders.KINDS:
                run_kind(self, uid, name, force=True)
            else:
                self.send(uid, "可测试：" + " ".join(list(reminders.KINDS) + ["intro"]))
            return True
        return False

    def handle_text(self, uid, text):
        is_new = not records.get_profile(uid)
        records.ensure_user(uid)
        cmd = text.strip()
        if is_new:
            # 第一次说话：先自我介绍；只是打招呼就等她回答称呼，否则接着处理她说的事
            greeting = bool(GREETING.match(cmd)) or not cmd
            if greeting:
                records.add_chat(uid, "user", text)   # 让聊天历史从她的招呼开始，模型知道自己问过称呼
            self.introduce(uid, ask_name=greeting)
            if greeting:
                return
        elif kv_get(f"intro:{uid}") != INTRO_VERSION:
            self.say(uid, WHATS_NEW)
            kv_set(f"intro:{uid}", INTRO_VERSION)
        if self.handle_command(uid, cmd):
            return
        self.deliver_pending(uid)
        self.handle_llm(uid, text)

    def handle_llm(self, uid, text):
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


# ---------- 各类主动推送（force=True 表示测试，跳过"已记录就不发"之类的判断）----------
def morning(bot, uid, force=False):
    p = records.get_profile(uid)
    if not p.get("city"):
        if force:
            bot.push(uid, "还不知道你在哪个城市，告诉我就能每天早上给你发天气啦～")
        return
    weather = get_weather(p["city"], p.get("province") or "")
    if weather.startswith(("天气接口出错", "没有找到城市")):
        bot.alert("weather", f"早安天气查询失败：{weather[:150]}\n检查和风天气的 Key、API Host 和额度。", 24)
        return
    text = llm.write(MORNING_INSTR, weather)
    bot.push(uid, text, nudge="早上好～今天的天气和提醒准备好啦，回我任意一句就能看到", keep_hours=5)


def meal(bot, uid, which, force=False):
    if not force and records.has_meal(uid, now().strftime("%Y-%m-%d"), which):
        return
    bot.push(uid, random.choice(MEAL_PROMPTS[which]))


def water(bot, uid, force=False):
    w = records.water_summary(uid)
    total, goal = w["喝水合计毫升"], w["喝水目标毫升"]
    if total >= goal and not force:
        return   # 已经喝够了，不打扰
    if total:
        text = f"今天已经喝了大约 {w['约几杯']:g} 杯（{total}ml）啦，离 {goal}ml 还差一点，再来一杯吧～ 喝完跟我说一声我帮你记上。"
    else:
        text = random.choice(WATER)
    bot.push(uid, text)


def weigh(bot, uid, force=False):
    since = (now().date() - datetime.timedelta(days=5)).isoformat()
    if not force and run("SELECT 1 FROM weights WHERE user_id=? AND day>=? LIMIT 1", (uid, since), fetch=True):
        return
    bot.push(uid, random.choice(WEIGH))


def weekly(bot, uid, force=False):
    stats = records.week_stats(uid)
    if not stats["有记录的天数"] and not stats["运动天数"]:
        if force:
            bot.push(uid, "这周还没有饮食或运动记录哦～先随手记一顿试试，有了记录我就能给你写小结啦。")
        return
    text = llm.write(WEEKLY_INSTR, json.dumps(stats, ensure_ascii=False))
    bot.push(uid, text, nudge="本周的小结写好啦，回我任意一句就能看到～", keep_hours=24 * 7)


def monthly(bot, uid, force=False):
    today = now().date()
    if force:
        month = today.strftime("%Y-%m")
    else:   # 每月1号发上个月的
        month = (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
    stats = records.month_stats(uid, month)
    if not stats.get("有饮食记录的天数") and not stats.get("运动天数"):
        if force:
            bot.push(uid, "这个月还没有饮食或运动记录哦，有了记录才能写月度小结～")
        return
    text = llm.write(MONTHLY_INSTR, json.dumps(stats, ensure_ascii=False))
    bot.push(uid, text, nudge="上个月的小结写好啦，回我任意一句就能看到～", keep_hours=24 * 7)


def night(bot, uid, force=False):
    s = records.day_summary(uid)
    if not s["饮食"] and not s["运动"] and not s["喝水"]:
        if force:
            bot.push(uid, "今天还没有记录哦，早点休息～")
        return
    bot.push(uid, llm.write(NIGHT_INSTR, json.dumps(s, ensure_ascii=False)))


def run_kind(bot, uid, kind, force=False):
    funcs = {"morning": morning, "water": water, "weigh": weigh, "weekly": weekly,
             "monthly": monthly, "night": night}
    try:
        if kind in ("breakfast", "lunch", "dinner"):
            meal(bot, uid, kind, force)
        else:
            funcs[kind](bot, uid, force)
    except Exception as e:  # noqa: BLE001
        log.exception("推送 %s 失败", kind)
        if kind in ("morning", "weekly", "monthly"):
            bot.alert(kind, f"{reminders.KINDS[kind]['name']}推送出错：{type(e).__name__}: {str(e)[:150]}", 24)


def tick(bot):
    """每分钟运行：给每个开着提醒的人，发到点的提醒。"""
    users = [r["user_id"] for r in run("SELECT user_id FROM profiles WHERE reminders=1", fetch=True)]
    for uid in users:
        for kind, _slot in reminders.due(uid):
            run_kind(bot, uid, kind)


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


def start_scheduler(bot):
    from apscheduler.schedulers.background import BackgroundScheduler
    s = BackgroundScheduler(timezone=str(config.TZ))
    kw = {"misfire_grace_time": 50, "coalesce": True, "max_instances": 1}
    s.add_job(tick, "cron", args=(bot,), second=5, **kw)                 # 每分钟
    s.add_job(balance_job, "cron", args=(bot,), hour=9, minute=5, **kw)
    s.add_job(reminders.cleanup_log, "cron", hour=3, minute=30, **kw)
    s.start()
    return s
